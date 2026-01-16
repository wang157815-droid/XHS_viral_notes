"""
通义千问 ASR 服务
支持本地文件和远程URL两种输入方式

修复说明：
- 2026-01-15: 修复 API 调用方式（本地文件用 MultiModalConversation）
- 2026-01-15: 添加音频时长检查（本地模式限制 3 分钟）
- 2026-01-15: 优化 URL 模式超时策略
- 2026-01-16: 本地文件改用 Base64 编码上传（避免 file:// URI 解析问题）
"""
import os
import asyncio
import time
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from loguru import logger
from dotenv import load_dotenv

from viral_agent.models.av_sync_model import TranscriptSegment, WordInfo

load_dotenv()


class ASRError(Exception):
    """ASR 识别错误"""
    pass


class AudioTooLongError(ASRError):
    """音频超过本地模式限制"""
    pass


class QwenASRService:
    """通义千问 ASR 服务"""

    # 模型选择策略：
    # - qwen3-asr-flash: 本地文件/短音频，最长3分钟，通过 MultiModalConversation
    # - qwen3-asr-flash-filetrans: 仅支持URL，最长12小时，通过 Transcription
    MODEL_LOCAL = "qwen3-asr-flash"           # 本地文件使用
    MODEL_URL = "qwen3-asr-flash-filetrans"   # 远程URL使用

    # 本地模式时长限制（秒）
    LOCAL_MAX_DURATION = 180  # 3 分钟

    # URL 模式超时配置
    URL_BASE_TIMEOUT = 300     # 基础超时 5 分钟
    URL_TIMEOUT_PER_MIN = 30   # 每分钟音频额外增加的超时

    def __init__(self):
        """初始化 ASR 服务"""
        self.api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('OPENAI_API_KEY')
        self.available = self._check_available()

        if self.available:
            logger.info(f"✅ 通义千问 ASR 已初始化 (本地:{self.MODEL_LOCAL}, URL:{self.MODEL_URL})")
        else:
            logger.warning("⚠️ ASR 未配置 API Key，语音识别功能不可用")

    def _check_available(self) -> bool:
        """检查服务是否可用"""
        if not self.api_key:
            return False
        try:
            import dashscope
            return True
        except ImportError:
            logger.warning("dashscope 未安装，请运行: pip install dashscope")
            return False

    def _get_audio_duration(self, audio_path: str) -> Optional[float]:
        """
        获取音频文件时长（秒）

        使用 ffprobe 获取时长，如果失败则返回 None
        """
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    audio_path
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
            logger.debug(f"获取音频时长失败: {e}")
        return None

    # 注：_to_file_uri 方法已移除，现在使用 Base64 编码上传（2026-01-16）

    async def transcribe(
        self,
        audio_path: str,
        language: str = 'zh'
    ) -> List[TranscriptSegment]:
        """
        语音转文字

        Args:
            audio_path: 音频文件路径（本地路径或URL）
            language: 语言代码

        Returns:
            转录片段列表

        Raises:
            ASRError: 识别失败
            AudioTooLongError: 音频超过本地模式限制（3分钟）
        """
        if not self.available:
            raise ASRError("ASR 服务未配置")

        is_url = audio_path.startswith('http://') or audio_path.startswith('https://')

        if not is_url and not os.path.exists(audio_path):
            raise ASRError(f"音频文件不存在: {audio_path}")

        logger.info(f"开始 ASR 识别: {audio_path}")
        start_time = time.time()

        try:
            loop = asyncio.get_event_loop()

            if is_url:
                # URL 使用异步转写接口
                result = await loop.run_in_executor(
                    None, self._transcribe_url, audio_path, None
                )
            else:
                # 本地文件：先检查时长
                duration = self._get_audio_duration(audio_path)
                if duration is not None:
                    logger.info(f"音频时长: {duration:.1f}s")
                    if duration > self.LOCAL_MAX_DURATION:
                        raise AudioTooLongError(
                            f"音频时长 {duration:.1f}s 超过本地模式限制 "
                            f"({self.LOCAL_MAX_DURATION}s)，请使用 URL 模式"
                        )

                # 本地文件使用 MultiModalConversation 接口
                result = await loop.run_in_executor(
                    None, self._transcribe_local, audio_path
                )

            elapsed = time.time() - start_time
            logger.success(f"✅ ASR 识别完成，耗时 {elapsed:.1f}s，共 {len(result)} 个片段")
            return result

        except (ASRError, AudioTooLongError):
            raise
        except Exception as e:
            raise ASRError(f"ASR 识别失败: {e}")

    def _transcribe_local(self, audio_path: str) -> List[TranscriptSegment]:
        """
        本地文件 ASR 识别
        使用 MultiModalConversation.call + Base64 编码

        实现方式：将音频文件读取后转为 Base64 data URI
        格式：data:audio/wav;base64,{base64_data}

        参考：https://help.aliyun.com/zh/model-studio/audio-language-model
        """
        import dashscope
        import base64
        from dashscope import MultiModalConversation

        dashscope.api_key = self.api_key

        abs_path = os.path.abspath(audio_path)
        logger.debug(f"本地 ASR: {abs_path}, 模型: {self.MODEL_LOCAL}")

        # 读取文件并转为 Base64 data URI
        try:
            with open(abs_path, 'rb') as f:
                audio_data = f.read()
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            # 格式：data:audio/wav;base64,{base64_data}
            # 显式指定 MIME 类型，确保 SDK 正确解析
            audio_uri = f"data:audio/wav;base64,{audio_base64}"
            logger.debug(f"音频 Base64 编码完成，大小: {len(audio_data)/1024:.1f}KB")
        except FileNotFoundError:
            raise ASRError(f"音频文件不存在: {abs_path}")
        except Exception as e:
            raise ASRError(f"读取音频文件失败: {e}")

        # 构建请求（使用 Base64 编码）
        messages = [
            {"role": "system", "content": [{"text": ""}]},
            {"role": "user", "content": [{"audio": audio_uri}]}
        ]

        try:
            response = MultiModalConversation.call(
                model=self.MODEL_LOCAL,
                messages=messages,
                result_format="message",
                asr_options={
                    "enable_timestamps": True,  # 启用时间戳
                    "enable_itn": True          # 启用逆文本正则化
                }
            )

            if response.status_code != 200:
                raise ASRError(f"ASR 调用失败: {response.message}")

            return self._parse_multimodal_result(response)

        except ASRError:
            raise
        except Exception as e:
            logger.error(f"本地 ASR 失败: {e}")
            raise ASRError(f"本地 ASR 失败: {e}")

    def _transcribe_url(
        self,
        audio_url: str,
        estimated_duration: Optional[float] = None
    ) -> List[TranscriptSegment]:
        """
        URL 文件 ASR 识别
        使用 Transcription.async_call（异步转写）

        Args:
            audio_url: 音频 URL
            estimated_duration: 预估音频时长（秒），用于计算超时
        """
        import dashscope
        from dashscope.audio.asr import Transcription

        dashscope.api_key = self.api_key

        logger.debug(f"URL ASR: {audio_url}, 模型: {self.MODEL_URL}")

        # 提交异步识别任务
        task_response = Transcription.async_call(
            model=self.MODEL_URL,
            file_urls=[audio_url],
        )

        if task_response.status_code != 200:
            raise ASRError(f"提交任务失败: {task_response.message}")

        task_id = task_response.output.task_id
        logger.debug(f"任务已提交: {task_id}")

        # 动态计算超时时间
        # 基础 5 分钟 + 每分钟音频额外 30 秒
        if estimated_duration:
            extra_timeout = (estimated_duration / 60) * self.URL_TIMEOUT_PER_MIN
            max_wait = int(self.URL_BASE_TIMEOUT + extra_timeout)
        else:
            # 未知时长，使用较长的默认超时（30 分钟）
            max_wait = 1800

        logger.debug(f"URL ASR 超时设置: {max_wait}s")

        wait_interval = 3  # 轮询间隔
        elapsed = 0

        while elapsed < max_wait:
            result = Transcription.fetch(task=task_id)

            if result.output.task_status == 'SUCCEEDED':
                return self._parse_transcription_result(result.output)
            elif result.output.task_status == 'FAILED':
                raise ASRError(f"识别失败: {result.output.message}")

            time.sleep(wait_interval)
            elapsed += wait_interval
            if elapsed % 30 == 0:  # 每 30 秒输出一次进度
                logger.debug(f"等待识别结果... {elapsed}s/{max_wait}s")

        raise ASRError(f"识别超时（等待 {max_wait}s）")

    def _parse_multimodal_result(self, response) -> List[TranscriptSegment]:
        """解析 MultiModalConversation 返回结果"""
        segments = []

        try:
            choices = response.output.choices
            if not choices:
                return segments

            message = choices[0].message
            content_list = message.content if hasattr(message, 'content') else []

            for content in content_list:
                if isinstance(content, dict):
                    text = content.get('text', '')
                    timestamps = content.get('timestamps', [])
                elif hasattr(content, 'text'):
                    text = content.text
                    timestamps = getattr(content, 'timestamps', [])
                else:
                    continue

                if not text:
                    continue

                if timestamps:
                    segments.extend(self._parse_timestamps(timestamps, text))
                else:
                    segments.append(TranscriptSegment(
                        start_time=0.0,
                        end_time=0.0,
                        text=text.strip(),
                        confidence=0.9,
                        words=[]
                    ))

        except Exception as e:
            logger.warning(f"解析 MultiModal 结果失败: {e}")
            try:
                text = self._extract_text_from_response(response)
                if text:
                    segments.append(TranscriptSegment(
                        start_time=0.0,
                        end_time=0.0,
                        text=text,
                        confidence=0.9,
                        words=[]
                    ))
            except Exception:
                pass

        return segments

    def _parse_timestamps(
        self,
        timestamps: List,
        full_text: str
    ) -> List[TranscriptSegment]:
        """解析时间戳列表"""
        segments = []

        for ts in timestamps:
            if isinstance(ts, dict):
                start = ts.get('start', 0) / 1000.0
                end = ts.get('end', 0) / 1000.0
                text = ts.get('text', '')
            else:
                start = getattr(ts, 'start', 0) / 1000.0
                end = getattr(ts, 'end', 0) / 1000.0
                text = getattr(ts, 'text', '')

            if text:
                segments.append(TranscriptSegment(
                    start_time=start,
                    end_time=end,
                    text=text,
                    confidence=0.9,
                    words=[]
                ))

        if not segments and full_text:
            segments.append(TranscriptSegment(
                start_time=0.0,
                end_time=0.0,
                text=full_text.strip(),
                confidence=0.9,
                words=[]
            ))

        return segments

    def _extract_text_from_response(self, response) -> str:
        """从响应中提取纯文本"""
        try:
            choices = response.output.choices
            if choices:
                message = choices[0].message
                content = message.content
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    texts = []
                    for c in content:
                        if isinstance(c, dict):
                            texts.append(c.get('text', ''))
                        elif hasattr(c, 'text'):
                            texts.append(c.text)
                    return ' '.join(filter(None, texts))
        except Exception:
            pass
        return ''

    def _parse_transcription_result(self, output) -> List[TranscriptSegment]:
        """解析 Transcription（异步转写）返回结果"""
        segments = []
        results = getattr(output, 'results', None) or []

        for item in results:
            transcripts = getattr(item, 'transcription', None)
            if not transcripts:
                continue

            for transcript in transcripts:
                sentences = getattr(transcript, 'sentences', [])
                for sentence in sentences:
                    words = []
                    word_list = getattr(sentence, 'words', [])

                    for word_info in word_list:
                        words.append(WordInfo(
                            word=getattr(word_info, 'text', ''),
                            start_time=getattr(word_info, 'begin_time', 0) / 1000.0,
                            end_time=getattr(word_info, 'end_time', 0) / 1000.0,
                            confidence=getattr(word_info, 'confidence', 0.0)
                        ))

                    segments.append(TranscriptSegment(
                        start_time=getattr(sentence, 'begin_time', 0) / 1000.0,
                        end_time=getattr(sentence, 'end_time', 0) / 1000.0,
                        text=getattr(sentence, 'text', ''),
                        confidence=0.9,
                        words=words
                    ))

        if not segments:
            full_text = self._extract_full_text_from_transcription(output)
            if full_text:
                segments.append(TranscriptSegment(
                    start_time=0.0,
                    end_time=0.0,
                    text=full_text,
                    confidence=0.9,
                    words=[]
                ))

        return segments

    def _extract_full_text_from_transcription(self, output) -> str:
        """从 Transcription 结果提取完整文本"""
        results = getattr(output, 'results', None) or []
        texts = []

        for item in results:
            transcripts = getattr(item, 'transcription', None)
            if transcripts:
                for t in transcripts:
                    text = getattr(t, 'text', '')
                    if text:
                        texts.append(text)

        return ' '.join(texts)

    def get_full_text(self, segments: List[TranscriptSegment]) -> str:
        """从转录片段中获取完整文本"""
        return ' '.join(seg.text for seg in segments if seg.text)
