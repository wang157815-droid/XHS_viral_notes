"""
通义千问 ASR 服务
使用 qwen3-asr-flash-filetrans 进行录音文件识别
"""
import os
import asyncio
import time
from typing import List, Optional
from loguru import logger
from dotenv import load_dotenv

from viral_agent.models.av_sync_model import TranscriptSegment, WordInfo

load_dotenv()


class ASRError(Exception):
    """ASR 识别错误"""
    pass


class QwenASRService:
    """通义千问 ASR 服务"""

    def __init__(self):
        """初始化 ASR 服务"""
        self.api_key = os.getenv('DASHSCOPE_API_KEY') or os.getenv('OPENAI_API_KEY')
        self.model = "qwen3-asr-flash-filetrans"
        self.available = self._check_available()

        if self.available:
            logger.info(f"✅ 通义千问 ASR 已初始化: {self.model}")
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

    async def transcribe(
        self,
        audio_path: str,
        language: str = 'zh'
    ) -> List[TranscriptSegment]:
        """
        语音转文字

        Args:
            audio_path: 音频文件路径
            language: 语言代码

        Returns:
            转录片段列表

        Raises:
            ASRError: 识别失败
        """
        if not self.available:
            raise ASRError("ASR 服务未配置")

        if not os.path.exists(audio_path):
            raise ASRError(f"音频文件不存在: {audio_path}")

        logger.info(f"开始 ASR 识别: {audio_path}")
        start_time = time.time()

        try:
            # 在线程池中运行同步的 ASR 调用
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._transcribe_sync,
                audio_path,
                language
            )

            elapsed = time.time() - start_time
            logger.success(f"✅ ASR 识别完成，耗时 {elapsed:.1f}s，共 {len(result)} 个片段")
            return result

        except Exception as e:
            raise ASRError(f"ASR 识别失败: {e}")

    def _transcribe_sync(
        self,
        audio_path: str,
        language: str
    ) -> List[TranscriptSegment]:
        """同步执行 ASR 识别"""
        import dashscope
        from dashscope.audio.qwen_asr import QwenTranscription

        dashscope.api_key = self.api_key

        # 提交异步识别任务
        logger.debug("提交 ASR 任务...")
        task_response = QwenTranscription.async_call(
            model=self.model,
            file_urls=[audio_path] if audio_path.startswith('http') else None,
            file_path=audio_path if not audio_path.startswith('http') else None,
        )

        if task_response.status_code != 200:
            raise ASRError(f"提交任务失败: {task_response.message}")

        task_id = task_response.output.task_id
        logger.debug(f"任务已提交: {task_id}")

        # 轮询等待结果
        max_wait = 300  # 最长等待 5 分钟
        wait_interval = 2
        elapsed = 0

        while elapsed < max_wait:
            result = QwenTranscription.fetch(task=task_id)

            if result.output.task_status == 'SUCCEEDED':
                return self._parse_result(result.output)
            elif result.output.task_status == 'FAILED':
                raise ASRError(f"识别失败: {result.output.message}")

            time.sleep(wait_interval)
            elapsed += wait_interval
            logger.debug(f"等待识别结果... {elapsed}s")

        raise ASRError("识别超时")

    def _parse_result(self, output) -> List[TranscriptSegment]:
        """解析 ASR 结果"""
        segments = []

        # 获取转录结果
        results = getattr(output, 'results', None) or []

        for item in results:
            transcripts = getattr(item, 'transcription', None)
            if not transcripts:
                continue

            for transcript in transcripts:
                # 解析句子
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
                        confidence=0.0,
                        words=words
                    ))

        # 如果没有解析到句子级别，尝试解析整体文本
        if not segments:
            full_text = self._extract_full_text(output)
            if full_text:
                segments.append(TranscriptSegment(
                    start_time=0.0,
                    end_time=0.0,
                    text=full_text,
                    confidence=0.0,
                    words=[]
                ))

        return segments

    def _extract_full_text(self, output) -> str:
        """提取完整文本（备用方案）"""
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
