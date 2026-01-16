"""
帧+ASR联合分析器
将视频帧和语音转录联合发送给多模态AI进行语义分析
"""
import os
import json
import base64
import asyncio
import time
from typing import List, Dict, Any, Optional
from loguru import logger
from dotenv import load_dotenv
import aiohttp

from viral_agent.models.av_sync_model import AVSyncResult, VideoFrame, TranscriptSegment
from viral_agent.models.frame_asr_model import (
    FrameASRAnalysisResult,
    FrameMomentAnalysis,
    ContentTransition,
    ProductPlacementAnalysis
)
from viral_agent.prompts.frame_asr_prompts import (
    FRAME_ASR_JOINT_PROMPT,
    FRAME_ASR_KEY_FRAMES_PROMPT,
    FRAME_TIMELINE_TEMPLATE
)

load_dotenv()


class FrameASRJointAnalyzer:
    """帧+ASR联合分析器"""

    def __init__(
        self,
        sample_strategy: str = None,
        max_frames_per_call: int = None,
        api_delay: float = None
    ):
        """
        初始化联合分析器

        Args:
            sample_strategy: 采样策略 (all/key/interval)
            max_frames_per_call: 单次API调用最多帧数
            api_delay: API调用间隔（秒）
        """
        # 从环境变量读取配置，参数优先
        self.sample_strategy = sample_strategy or os.getenv(
            'FRAME_ASR_SAMPLE_STRATEGY', 'key'
        )
        self.max_frames_per_call = max_frames_per_call or int(os.getenv(
            'FRAME_ASR_MAX_FRAMES', '5'
        ))
        self.api_delay = api_delay or float(os.getenv(
            'FRAME_ASR_API_DELAY', '2.0'
        ))

        # 多模态API配置
        self.api_base = os.getenv('MULTIMODAL_API_BASE')
        self.api_key = os.getenv('MULTIMODAL_API_KEY')
        self.model_name = os.getenv('MULTIMODAL_MODEL_NAME', 'qwen3-vl-flash')

        self.available = bool(self.api_key and self.api_base)
        if not self.available:
            logger.warning("帧+ASR联合分析器不可用：缺少多模态API配置")

        logger.info(
            f"帧+ASR联合分析器初始化: 策略={self.sample_strategy}, "
            f"最大帧数={self.max_frames_per_call}, 可用={self.available}"
        )

    async def analyze(
        self,
        av_sync_result: AVSyncResult,
        title: str = "",
        description: str = ""
    ) -> FrameASRAnalysisResult:
        """
        执行帧+ASR联合分析

        Args:
            av_sync_result: 音画同步分析结果（包含帧和转录）
            title: 视频标题
            description: 视频描述

        Returns:
            联合分析结果
        """
        result = FrameASRAnalysisResult(
            note_id=av_sync_result.note_id,
            video_url=av_sync_result.video_url,
            video_duration=av_sync_result.video_duration,
            full_transcript=av_sync_result.full_text
        )

        if not self.available:
            result.status = "failed"
            result.error_message = "多模态API未配置"
            return result

        if not av_sync_result.frames:
            result.status = "failed"
            result.error_message = "无可用帧数据"
            return result

        start_time = time.time()

        try:
            # Step 1: 根据策略选择帧
            selected_frames = self._select_frames(
                av_sync_result.frames,
                av_sync_result.transcript
            )
            result.frame_count = len(selected_frames)
            logger.info(f"选择了 {len(selected_frames)} 帧进行分析")

            # Step 2: 为每帧匹配语音
            matched_speeches = [
                self._match_speech_to_frame(frame, av_sync_result.transcript)
                for frame in selected_frames
            ]

            # Step 3: 分批调用AI分析
            all_frame_analyses = []
            for i in range(0, len(selected_frames), self.max_frames_per_call):
                if i > 0:
                    await asyncio.sleep(self.api_delay)

                batch_frames = selected_frames[i:i + self.max_frames_per_call]
                batch_speeches = matched_speeches[i:i + self.max_frames_per_call]

                batch_result = await self._analyze_batch(
                    frames=batch_frames,
                    speeches=batch_speeches,
                    title=title,
                    description=description,
                    duration=av_sync_result.video_duration,
                    full_transcript=av_sync_result.full_text
                )

                if batch_result:
                    all_frame_analyses.extend(batch_result.get('frame_analyses', []))

                    # 只从第一批获取全局信息
                    if i == 0:
                        result.content_structure = batch_result.get(
                            'content_structure', ''
                        )
                        result.visual_speech_summary = batch_result.get(
                            'visual_speech_summary', ''
                        )
                        result.content_rhythm = batch_result.get(
                            'content_rhythm', ''
                        )
                        result.recommendations = batch_result.get(
                            'recommendations', []
                        )

                        # 解析产品植入
                        pp = batch_result.get('product_placement')
                        if pp:
                            result.product_placement = ProductPlacementAnalysis(
                                first_visual_time=pp.get('first_visual_time'),
                                first_mention_time=pp.get('first_mention_time'),
                                placement_style=pp.get('placement_style', 'unknown'),
                                visual_speech_sync=pp.get('visual_speech_sync', 'unknown'),
                                naturalness_score=pp.get('naturalness_score', 5)
                            )

                        # 解析转折点
                        for t in batch_result.get('transitions', []):
                            result.transitions.append(ContentTransition(
                                timestamp=t.get('timestamp', 0),
                                from_type=t.get('from_type', ''),
                                to_type=t.get('to_type', ''),
                                transition_method=t.get('transition_method', ''),
                                smoothness_score=t.get('smoothness_score', 5)
                            ))

            # 解析帧分析结果
            for fa in all_frame_analyses:
                result.frame_analyses.append(FrameMomentAnalysis(
                    frame_index=fa.get('frame_index', 0),
                    timestamp=fa.get('timestamp', 0.0),
                    visual_content=fa.get('visual_content', ''),
                    speech_content=fa.get('speech_content', ''),
                    visual_speech_relation=fa.get('visual_speech_relation', ''),
                    content_type=fa.get('content_type', ''),
                    product_visible=fa.get('product_visible', False),
                    product_mentioned=fa.get('product_mentioned', False),
                    engagement_score=fa.get('engagement_score', 5)
                ))

            result.status = "success"
            result.process_time = time.time() - start_time
            logger.success(
                f"✅ 帧+ASR联合分析完成，耗时 {result.process_time:.1f}s"
            )

        except Exception as e:
            result.status = "failed"
            result.error_message = str(e)
            logger.error(f"帧+ASR联合分析失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return result

    def _select_frames(
        self,
        frames: List[VideoFrame],
        transcript: List[TranscriptSegment]
    ) -> List[VideoFrame]:
        """根据采样策略选择帧"""
        if not frames:
            return []

        if self.sample_strategy == 'all':
            # 全部帧（最多max_frames_per_call * 3）
            max_total = self.max_frames_per_call * 3
            return frames[:max_total]

        elif self.sample_strategy == 'interval':
            # 固定间隔采样
            if len(frames) <= self.max_frames_per_call:
                return frames
            step = len(frames) // self.max_frames_per_call
            return [frames[i] for i in range(0, len(frames), max(1, step))]

        else:  # 'key' 策略（默认）
            # 关键帧：开头、结尾、中间关键点
            selected = []
            indices = set()

            # 1. 第一帧（开头）
            indices.add(0)

            # 2. 最后一帧（结尾）
            indices.add(len(frames) - 1)

            # 3. 中间帧（均匀分布）
            if len(frames) > 2 and self.max_frames_per_call > 2:
                # 确保 mid_count 至少为1，避免除零
                mid_count = max(1, min(self.max_frames_per_call - 2, len(frames) - 2))
                step = (len(frames) - 1) / (mid_count + 1)
                for i in range(1, mid_count + 1):
                    indices.add(int(i * step))

            # 按索引排序
            for idx in sorted(indices):
                if idx < len(frames):
                    selected.append(frames[idx])

            return selected[:self.max_frames_per_call]

    def _match_speech_to_frame(
        self,
        frame: VideoFrame,
        transcript: List[TranscriptSegment]
    ) -> str:
        """为帧匹配对应时段的语音文本"""
        if not transcript:
            return ""

        # 帧时间戳前后2秒范围
        start = frame.timestamp - 2.0
        end = frame.timestamp + 2.0

        matched_texts = []
        for seg in transcript:
            # 检查时间段是否重叠
            if seg.end_time >= start and seg.start_time <= end:
                matched_texts.append(seg.text)

        return " ".join(matched_texts)

    async def _analyze_batch(
        self,
        frames: List[VideoFrame],
        speeches: List[str],
        title: str,
        description: str,
        duration: float,
        full_transcript: str
    ) -> Optional[Dict[str, Any]]:
        """批量分析一组帧"""
        try:
            # 构建帧时间轴描述（包含每帧对应的语音）
            frame_lines = []
            for i, (f, speech) in enumerate(zip(frames, speeches)):
                speech_preview = speech[:80] + '...' if len(speech) > 80 else speech
                frame_lines.append(
                    f"帧{f.frame_index}（{f.timestamp:.1f}s）: [图片{i}]\n"
                    f"  对应语音: {speech_preview or '（无语音）'}"
                )
            frame_timeline = "\n".join(frame_lines)

            # 选择Prompt
            prompt = FRAME_ASR_KEY_FRAMES_PROMPT.format(
                title=title or "无标题",
                description=description or "",
                duration=duration,
                frame_count=len(frames),
                frame_timeline=frame_timeline,
                full_transcript=full_transcript[:1500] if full_transcript else "无语音"
            )

            # 构建消息（包含多张图片）
            messages = self._build_multi_image_messages(
                frames=frames,
                prompt=prompt
            )

            # 调用API
            response = await self._call_multimodal_api(messages)

            if not response or response.startswith("API"):
                logger.warning(f"API调用失败: {response}")
                return None

            # 解析JSON响应
            return self._parse_json_response(response)

        except Exception as e:
            logger.error(f"批量分析失败: {e}")
            return None

    def _build_multi_image_messages(
        self,
        frames: List[VideoFrame],
        prompt: str
    ) -> List[Dict[str, Any]]:
        """构建多图片消息"""
        # 构建content列表，先加入文本提示
        user_content = [{"type": "text", "text": prompt}]

        # 添加每一帧图片
        for frame in frames:
            if frame.frame_path and os.path.exists(frame.frame_path):
                # 读取图片并转为base64
                try:
                    with open(frame.frame_path, 'rb') as f:
                        image_data = f.read()
                    base64_image = base64.b64encode(image_data).decode('utf-8')

                    # 判断图片格式
                    if frame.frame_path.lower().endswith('.png'):
                        mime_type = 'image/png'
                    else:
                        mime_type = 'image/jpeg'

                    image_url = f"data:{mime_type};base64,{base64_image}"

                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": image_url}
                    })
                except Exception as e:
                    logger.warning(f"读取帧图片失败 {frame.frame_path}: {e}")

        return [
            {"role": "system", "content": "你是一个专业的短视频内容分析师。"},
            {"role": "user", "content": user_content}
        ]

    async def _call_multimodal_api(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 2000,
        max_retries: int = 3
    ) -> str:
        """调用多模态API"""
        if not self.api_key:
            return "API密钥未配置"

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': self.model_name,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': 0.3
        }

        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f'{self.api_base}/chat/completions',
                        headers=headers,
                        json=data,
                        timeout=aiohttp.ClientTimeout(total=120)
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result['choices'][0]['message']['content']
                        elif response.status == 429:
                            wait_time = (2 ** attempt) * 5
                            logger.warning(
                                f"API限流(429)，第{attempt + 1}次重试，"
                                f"等待{wait_time}秒..."
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            error_text = await response.text()
                            logger.error(
                                f"API调用失败: {response.status} - {error_text[:200]}"
                            )
                            return f"API调用失败: {response.status}"

            except asyncio.TimeoutError:
                logger.warning(f"API超时，第{attempt + 1}次重试...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"API调用异常: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)

        return "API调用失败: 超过最大重试次数"

    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """解析JSON响应"""
        try:
            # 尝试直接解析
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试提取JSON块
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        # 尝试提取```json```块
        try:
            if '```json' in response:
                start = response.find('```json') + 7
                end = response.find('```', start)
                if end > start:
                    return json.loads(response[start:end].strip())
        except json.JSONDecodeError:
            pass

        logger.warning(f"无法解析JSON响应: {response[:200]}")
        return None
