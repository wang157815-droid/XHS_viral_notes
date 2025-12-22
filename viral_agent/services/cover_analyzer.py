"""
封面分析器
负责分析笔记封面图片的视觉特征和文字内容
"""
import os
import requests
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from PIL import Image, ImageFilter
from io import BytesIO
import numpy as np
from loguru import logger
import jieba
from concurrent.futures import ThreadPoolExecutor, as_completed

# 尝试导入OCR引擎（优先EasyOCR，备选PaddleOCR）
OCR_ENGINE = None
OCR_AVAILABLE = False

# 先尝试EasyOCR（更稳定）
try:
    import easyocr
    OCR_ENGINE = 'easy'
    OCR_AVAILABLE = True
except Exception:
    pass

# 如果EasyOCR不可用，尝试PaddleOCR
if not OCR_AVAILABLE:
    try:
        from paddleocr import PaddleOCR
        OCR_ENGINE = 'paddle'
        OCR_AVAILABLE = True
    except Exception:  # 捕获所有异常，不只是ImportError
        pass


class CoverAnalyzer:
    """封面分析器"""

    def __init__(self, cache_dir: str = "datas/cover_cache"):
        """
        初始化封面分析器

        Args:
            cache_dir: 封面图片缓存目录
        """
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        # 初始化OCR
        self.ocr = None
        self.ocr_engine = None

        if OCR_AVAILABLE:
            if OCR_ENGINE == 'paddle':
                # 尝试初始化PaddleOCR
                init_configs = [
                    {'det_limit_side_len': 960, 'rec_batch_num': 6, 'lang': 'ch', 'show_log': False},
                    {'lang': 'ch', 'show_log': False},
                    {'lang': 'ch'},
                    {},
                ]

                for i, config in enumerate(init_configs, 1):
                    try:
                        logger.info(f"尝试PaddleOCR方案{i}: {config}")
                        from paddleocr import PaddleOCR
                        self.ocr = PaddleOCR(**config)
                        self.ocr_engine = 'paddle'
                        logger.success(f"✓ PaddleOCR初始化成功（方案{i}）")
                        break
                    except Exception as e:
                        logger.warning(f"PaddleOCR方案{i}失败: {type(e).__name__}")

                if not self.ocr:
                    logger.warning("PaddleOCR初始化失败，尝试EasyOCR...")

            if not self.ocr and OCR_ENGINE == 'easy' or (OCR_ENGINE == 'paddle' and not self.ocr):
                # 尝试初始化EasyOCR
                try:
                    logger.info("正在初始化EasyOCR（首次使用会下载模型，请稍候）...")
                    import easyocr
                    self.ocr = easyocr.Reader(['ch_sim', 'en'], gpu=False)
                    self.ocr_engine = 'easy'
                    logger.success("✓ EasyOCR初始化成功")
                except Exception as e:
                    logger.error(f"EasyOCR初始化失败: {e}")

            if not self.ocr:
                logger.error("所有OCR引擎初始化均失败")
                logger.warning("封面文字识别功能已禁用，其他分析功能正常")
                logger.info("修复建议:")
                logger.info("  方案A（推荐）: pip install easyocr")
                logger.info("  方案B: pip install --upgrade paddlepaddle paddleocr")
        else:
            logger.warning("未安装OCR引擎，封面文字识别功能已禁用")
            logger.info("安装建议: pip install easyocr")

    def _download_images_parallel(
        self,
        tasks: List[Tuple[str, str]],
        max_workers: int = 10
    ) -> Dict[str, Image.Image]:
        """
        并行下载多张图片（性能优化：使用线程池并发下载）

        Args:
            tasks: [(url, note_id), ...] 下载任务列表
            max_workers: 最大并发线程数

        Returns:
            {note_id: Image} 下载成功的图片字典
        """
        results = {}

        if not tasks:
            return results

        logger.info(f"并行下载 {len(tasks)} 张图片（并发数: {max_workers}）...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有下载任务
            future_to_task = {
                executor.submit(self._download_image, url, note_id): (url, note_id)
                for url, note_id in tasks
            }

            # 收集结果
            completed = 0
            for future in as_completed(future_to_task):
                url, note_id = future_to_task[future]
                completed += 1
                try:
                    image = future.result()
                    if image:
                        results[note_id] = image
                except Exception as e:
                    logger.debug(f"下载图片失败 {note_id}: {e}")

                # 每下载20张打印一次进度
                if completed % 20 == 0:
                    logger.info(f"下载进度: {completed}/{len(tasks)}")

        logger.success(f"图片下载完成: 成功 {len(results)}/{len(tasks)}")
        return results

    def _analyze_images_parallel(
        self,
        images: Dict[str, Image.Image],
        max_workers: int = 4
    ) -> List[Dict[str, Any]]:
        """
        并行分析多张图片（性能优化：使用线程池并发处理OCR和其他分析）

        注意：虽然Python有GIL，但OCR底层的C扩展和numpy操作会释放GIL，
        因此使用线程池仍然能获得一定的并行加速。

        Args:
            images: {note_id: Image} 图片字典
            max_workers: 最大并发线程数（建议2-4，OCR内存占用较大）

        Returns:
            图片特征列表
        """
        results = []

        if not images:
            return results

        logger.info(f"并行分析 {len(images)} 张图片（线程数: {max_workers}）...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有分析任务
            future_to_id = {
                executor.submit(self._analyze_image_features, image, note_id): note_id
                for note_id, image in images.items()
            }

            # 收集结果
            completed = 0
            for future in as_completed(future_to_id):
                note_id = future_to_id[future]
                completed += 1
                try:
                    features = future.result()
                    if features:
                        results.append(features)
                except Exception as e:
                    logger.debug(f"分析图片失败 {note_id}: {e}")

                # 每分析10张打印一次进度
                if completed % 10 == 0:
                    logger.info(f"分析进度: {completed}/{len(images)}")

        logger.success(f"图片分析完成: 成功 {len(results)}/{len(images)}")
        return results

    def analyze_covers(self, notes_with_covers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量分析封面特征（优化版：并行下载图片）

        Args:
            notes_with_covers: 包含封面URL的笔记列表

        Returns:
            封面分析结果汇总
        """
        logger.info(f"开始分析 {len(notes_with_covers)} 个封面...")

        # 第一步：收集所有需要下载的任务
        download_tasks = []
        note_map = {}  # note_id -> note
        for i, note in enumerate(notes_with_covers):
            cover_url = self._get_cover_url(note)
            if cover_url:
                note_id = note.get('note_id', f'note_{i}')
                download_tasks.append((cover_url, note_id))
                note_map[note_id] = note

        # 第二步：并行下载所有图片
        images = self._download_images_parallel(download_tasks, max_workers=10)

        # 第三步：并行分析已下载的图片（OCR + 颜色 + 布局）
        all_cover_features = self._analyze_images_parallel(images, max_workers=4)

        # 第四步：收集各类特征
        text_contents = []
        color_features = []
        layout_features = []

        for features in all_cover_features:
            if features.get('text_content'):
                text_contents.extend(features['text_content'])
            if features.get('dominant_colors'):
                color_features.append(features['dominant_colors'])
            if features.get('layout'):
                layout_features.append(features['layout'])

        # 汇总分析结果
        summary = self._summarize_cover_features(
            all_cover_features,
            text_contents,
            color_features,
            layout_features
        )

        logger.success(f"封面分析完成，成功分析 {len(all_cover_features)} 个")
        return summary

    def _analyze_image_features(self, image: Image.Image, note_id: str) -> Optional[Dict[str, Any]]:
        """
        分析单张已下载图片的特征（内部方法）

        Args:
            image: PIL Image对象
            note_id: 笔记ID

        Returns:
            图片特征字典
        """
        try:
            features = {
                'note_id': note_id,
                'size': image.size,
                'aspect_ratio': round(image.width / image.height, 2)
            }

            # 1. 颜色分析
            features['dominant_colors'] = self._analyze_colors(image)

            # 2. 文字识别（OCR）
            if self.ocr:
                features['text_content'] = self._extract_text(image)
                features['text_layout'] = self._analyze_text_layout(features['text_content'])

            # 3. 布局分析
            features['layout'] = self._analyze_layout(image)

            # 4. 视觉元素分析
            features['visual_elements'] = self._analyze_visual_elements(image)

            return features

        except Exception as e:
            logger.error(f"分析图片特征失败 {note_id}: {e}")
            return None

    def analyze_all_images(
        self,
        notes: List[Dict[str, Any]],
        max_images_per_note: int = 9,
        skip_cover: bool = True
    ) -> Dict[str, Any]:
        """
        分析笔记的所有图片（优化版：并行下载图片）

        注意：此方法只分析图文笔记的内容图，视频笔记跳过（视频的image_list是预览帧，不是内容图）
        视频笔记的封面分析由 analyze_covers() 方法处理

        Args:
            notes: 笔记列表
            max_images_per_note: 每篇笔记最多分析的图片数量
            skip_cover: 是否跳过封面（第一张图），默认True避免与analyze_covers重复

        Returns:
            所有图片的分析结果汇总
        """
        logger.info(f"开始分析所有图片（每篇最多{max_images_per_note}张）...")

        # 统计笔记类型
        video_note_count = 0
        image_note_count = 0

        # 第一步：收集所有需要下载的任务
        download_tasks = []
        task_metadata = {}  # cache_id -> {note_id, image_index, is_cover}
        note_image_counts = []

        for note_idx, note in enumerate(notes):
            note_id = note.get('note_id', f'note_{note_idx}')
            note_type = note.get('note_type', '')

            # 跳过视频笔记的内容图分析
            if note_type == '视频':
                video_note_count += 1
                logger.debug(f"跳过视频笔记 {note_id} 的内容图分析")
                continue

            image_note_count += 1

            # 获取笔记的所有图片URL
            image_urls = self._get_all_image_urls(note, max_images_per_note)
            if not image_urls:
                logger.debug(f"笔记 {note_id} 没有图片")
                continue

            note_image_counts.append(len(image_urls))

            # 收集下载任务
            for img_idx, img_url in enumerate(image_urls):
                cache_id = f"{note_id}_img_{img_idx}"
                download_tasks.append((img_url, cache_id))
                task_metadata[cache_id] = {
                    'note_id': note_id,
                    'image_index': img_idx,
                    'is_cover': (img_idx == 0)
                }

        # 第二步：并行下载所有图片
        images = self._download_images_parallel(download_tasks, max_workers=15)

        # 第三步：并行分析已下载的图片（OCR + 颜色 + 布局）
        raw_features = self._analyze_images_parallel(images, max_workers=4)

        # 第四步：为分析结果添加元数据
        all_image_features = []
        all_text_contents = []

        for features in raw_features:
            cache_id = features.get('note_id', '')
            meta = task_metadata.get(cache_id, {})
            features['note_id'] = meta.get('note_id', cache_id)
            features['image_index'] = meta.get('image_index', 0)
            features['is_cover'] = meta.get('is_cover', False)
            all_image_features.append(features)

            # 收集文字内容
            if features.get('text_content'):
                all_text_contents.extend(features['text_content'])

        # 汇总分析结果
        summary = self._summarize_all_images(
            all_image_features,
            all_text_contents,
            note_image_counts,
            image_note_count
        )

        # 增加笔记类型统计
        summary['note_type_stats'] = {
            'total_notes': len(notes),
            'image_notes_analyzed': image_note_count,
            'video_notes_skipped': video_note_count
        }

        logger.success(f"所有图片分析完成，共分析 {len(all_image_features)} 张图片（图文笔记 {image_note_count} 篇，跳过视频笔记 {video_note_count} 篇）")
        return summary

    def _get_all_image_urls(self, note: Dict[str, Any], max_count: int) -> List[str]:
        """
        获取笔记的所有图片URL

        Args:
            note: 笔记数据
            max_count: 最大图片数量

        Returns:
            图片URL列表
        """
        image_urls = []

        # 从image_list获取所有图片
        if note.get('image_list'):
            for img in note['image_list'][:max_count]:
                if isinstance(img, dict):
                    url = img.get('url_default') or img.get('url_pre') or img.get('url', '')
                else:
                    url = img
                if url:
                    image_urls.append(url)

        return image_urls

    def _summarize_all_images(
        self,
        all_features: List[Dict],
        text_contents: List[str],
        image_counts: List[int],
        note_count: int
    ) -> Dict[str, Any]:
        """
        汇总所有图片的分析结果

        Args:
            all_features: 所有图片的特征
            text_contents: 所有文字内容
            image_counts: 每篇笔记的图片数量
            note_count: 笔记总数

        Returns:
            汇总结果
        """
        summary = {
            'total_notes': note_count,
            'total_images': len(all_features),
            'avg_images_per_note': round(sum(image_counts) / len(image_counts), 1) if image_counts else 0,
            'image_count_distribution': {
                '1-3张': sum(1 for c in image_counts if 1 <= c <= 3),
                '4-6张': sum(1 for c in image_counts if 4 <= c <= 6),
                '7-9张': sum(1 for c in image_counts if 7 <= c <= 9)
            }
        }

        # 文字分析（所有图片的OCR结果）
        if text_contents:
            import jieba
            words = []
            for text in text_contents:
                words.extend(list(jieba.cut(text)))
            from collections import Counter
            word_freq = Counter([w for w in words if len(w) > 1])

            summary['ocr_text_analysis'] = {
                'total_images_with_text': len([f for f in all_features if f.get('text_content')]),
                'text_coverage_rate': round(
                    len([f for f in all_features if f.get('text_content')]) / len(all_features) * 100, 1
                ) if all_features else 0,
                'top_keywords': [{'word': w, 'count': c} for w, c in word_freq.most_common(30)],
                'total_text_items': len(text_contents)
            }

        # 封面 vs 非封面对比
        cover_features = [f for f in all_features if f.get('is_cover')]
        non_cover_features = [f for f in all_features if not f.get('is_cover')]

        summary['cover_vs_other'] = {
            'cover_count': len(cover_features),
            'other_count': len(non_cover_features),
            'cover_text_rate': round(
                len([f for f in cover_features if f.get('text_content')]) / len(cover_features) * 100, 1
            ) if cover_features else 0,
            'other_text_rate': round(
                len([f for f in non_cover_features if f.get('text_content')]) / len(non_cover_features) * 100, 1
            ) if non_cover_features else 0
        }

        return summary

    def analyze_single_cover(self, cover_url: str, note_id: str) -> Optional[Dict[str, Any]]:
        """
        分析单个封面

        Args:
            cover_url: 封面图片URL
            note_id: 笔记ID（用于缓存）

        Returns:
            封面特征字典
        """
        try:
            # 下载图片
            image = self._download_image(cover_url, note_id)
            if not image:
                return None

            features = {
                'note_id': note_id,
                'size': image.size,
                'aspect_ratio': round(image.width / image.height, 2)
            }

            # 1. 颜色分析
            features['dominant_colors'] = self._analyze_colors(image)

            # 2. 文字识别（OCR）
            if self.ocr:
                features['text_content'] = self._extract_text(image)
                features['text_layout'] = self._analyze_text_layout(features['text_content'])

            # 3. 布局分析
            features['layout'] = self._analyze_layout(image)

            # 4. 视觉元素分析
            features['visual_elements'] = self._analyze_visual_elements(image)

            return features

        except Exception as e:
            logger.error(f"分析封面 {note_id} 失败: {e}")
            return None

    def _get_cover_url(self, note: Dict[str, Any]) -> Optional[str]:
        """
        从笔记数据中提取封面URL

        Args:
            note: 笔记数据

        Returns:
            封面URL
        """
        # 视频封面
        if note.get('video_cover'):
            # 视频封面也可能是dict，需要提取URL
            if isinstance(note['video_cover'], dict):
                return note['video_cover'].get('url_default') or note['video_cover'].get('url') or note['video_cover'].get('url_pre')
            return note['video_cover']

        # 图文第一张图
        if note.get('image_list') and len(note['image_list']) > 0:
            first_image = note['image_list'][0]
            # 如果是字典，提取URL
            if isinstance(first_image, dict):
                # 优先使用url_default，然后url_pre，最后url
                return first_image.get('url_default') or first_image.get('url_pre') or first_image.get('url', '')
            # 如果是字符串，直接返回
            return first_image

        return None

    def _download_image(self, url: str, note_id: str) -> Optional[Image.Image]:
        """
        下载并缓存图片

        Args:
            url: 图片URL
            note_id: 笔记ID

        Returns:
            PIL Image对象
        """
        try:
            # 检查缓存
            cache_path = os.path.join(self.cache_dir, f"{note_id}.jpg")
            if os.path.exists(cache_path):
                return Image.open(cache_path)

            # 下载图片
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                image = Image.open(BytesIO(response.content))

                # 转换为RGB（去除透明通道）
                if image.mode != 'RGB':
                    image = image.convert('RGB')

                # 缓存图片
                image.save(cache_path, 'JPEG')

                return image

        except Exception as e:
            logger.error(f"下载图片失败 {url}: {e}")

        return None

    def _analyze_colors(self, image: Image.Image) -> Dict[str, Any]:
        """
        分析图片主色调

        Args:
            image: PIL Image对象

        Returns:
            颜色特征
        """
        # 缩小图片以加快处理
        small_image = image.resize((150, 150))
        pixels = np.array(small_image)

        # 获取主色调
        pixels = pixels.reshape(-1, 3)

        # 使用简单的颜色统计（避免sklearn依赖）
        # 将RGB值量化到更粗的粒度
        quantized = (pixels // 64) * 64  # 量化到64级

        # 统计每种颜色出现的次数
        unique_colors, counts = np.unique(quantized, axis=0, return_counts=True)

        # 按出现次数排序，取前5个
        sorted_indices = np.argsort(counts)[::-1][:5]
        colors = unique_colors[sorted_indices]
        color_counts = counts[sorted_indices]
        total = np.sum(color_counts)

        # 计算每个颜色的占比
        dominant_colors = []
        for i, color in enumerate(colors):
            percentage = color_counts[i] / total * 100
            dominant_colors.append({
                'rgb': color.tolist(),
                'hex': '#{:02x}{:02x}{:02x}'.format(*color),
                'percentage': round(percentage, 1)
            })

        # 按占比排序
        dominant_colors.sort(key=lambda x: x['percentage'], reverse=True)

        # 判断色调类型
        avg_brightness = np.mean(pixels)
        color_style = 'bright' if avg_brightness > 127 else 'dark'

        # 判断色彩饱和度
        hsv_image = image.convert('HSV')
        hsv_pixels = np.array(hsv_image)
        avg_saturation = np.mean(hsv_pixels[:, :, 1])
        saturation_level = 'high' if avg_saturation > 127 else 'low'

        return {
            'dominant_colors': dominant_colors[:3],  # 前3个主色
            'color_style': color_style,
            'saturation_level': saturation_level,
            'avg_brightness': round(float(avg_brightness), 1)
        }

    def _extract_text(self, image: Image.Image) -> List[str]:
        """
        使用OCR提取图片中的文字

        Args:
            image: PIL Image对象

        Returns:
            识别出的文字列表
        """
        if not self.ocr:
            return []

        try:
            # 转换为numpy数组
            img_array = np.array(image)
            texts = []

            if self.ocr_engine == 'paddle':
                # PaddleOCR
                result = self.ocr.ocr(img_array, cls=True)
                if result and result[0]:
                    for line in result[0]:
                        if line and len(line) > 1 and line[1]:
                            text = line[1][0]  # 文字内容
                            confidence = line[1][1]  # 置信度
                            # 只保留置信度较高的文字
                            if confidence > 0.8 and len(text.strip()) > 0:
                                texts.append(text.strip())

            elif self.ocr_engine == 'easy':
                # EasyOCR
                result = self.ocr.readtext(img_array)
                for detection in result:
                    if len(detection) >= 2:
                        text = detection[1]  # 文字内容
                        confidence = detection[2] if len(detection) > 2 else 1.0  # 置信度
                        # 只保留置信度较高的文字
                        if confidence > 0.6 and len(text.strip()) > 0:
                            texts.append(text.strip())

            return texts

        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return []

    def _analyze_text_layout(self, texts: List[str]) -> Dict[str, Any]:
        """
        分析文字布局特征

        Args:
            texts: 识别出的文字列表

        Returns:
            文字布局特征
        """
        if not texts:
            return {'has_text': False}

        # 统计文字特征
        total_chars = sum(len(text) for text in texts)

        # 判断文字类型
        has_numbers = any(any(c.isdigit() for c in text) for text in texts)
        has_emoji = any(self._contains_emoji(text) for text in texts)

        # 识别关键词类型
        keyword_types = []
        keywords_map = {
            '测评': ['测评', '评测', '对比', 'PK', 'VS'],
            '推荐': ['推荐', '种草', '必买', '必入', '必备'],
            '教程': ['教程', '教学', '攻略', '方法', '步骤'],
            '优惠': ['折扣', '优惠', '特价', '秒杀', '福利'],
            '效果': ['效果', '前后', '对比', '改善', '提升']
        }

        for text in texts:
            for category, words in keywords_map.items():
                if any(word in text for word in words):
                    keyword_types.append(category)

        return {
            'has_text': True,
            'text_count': len(texts),
            'total_chars': total_chars,
            'avg_text_length': round(total_chars / len(texts), 1),
            'has_numbers': has_numbers,
            'has_emoji': has_emoji,
            'keyword_types': list(set(keyword_types)),
            'main_text': texts[0] if texts else ''  # 最主要的文字
        }

    def _analyze_layout(self, image: Image.Image) -> Dict[str, Any]:
        """
        分析图片布局

        Args:
            image: PIL Image对象

        Returns:
            布局特征
        """
        width, height = image.size

        # 判断图片方向
        if width > height:
            orientation = 'landscape'
        elif width < height:
            orientation = 'portrait'
        else:
            orientation = 'square'

        # 判断是否为拼图
        is_collage = self._detect_collage(image)

        # 判断是否有边框
        has_border = self._detect_border(image)

        return {
            'orientation': orientation,
            'aspect_ratio': round(width / height, 2),
            'is_collage': is_collage,
            'has_border': has_border,
            'resolution_category': self._categorize_resolution(width, height)
        }

    def _analyze_visual_elements(self, image: Image.Image) -> Dict[str, Any]:
        """
        分析视觉元素

        Args:
            image: PIL Image对象

        Returns:
            视觉元素特征
        """
        # 转换为灰度图
        gray_image = image.convert('L')
        pixels = np.array(gray_image)

        # 计算对比度
        contrast = pixels.std()

        # 检测是否为纯色背景
        is_solid_bg = contrast < 20

        # 检测是否为产品图
        is_product = self._detect_product_image(image)

        # 检测是否为人物图
        has_people = self._detect_people(image)

        return {
            'contrast_level': 'high' if contrast > 50 else 'low',
            'is_solid_background': is_solid_bg,
            'is_product_image': is_product,
            'has_people': has_people,
            'visual_complexity': self._calculate_complexity(pixels)
        }

    def _contains_emoji(self, text: str) -> bool:
        """检查文本是否包含emoji"""
        import re
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F"
            "|\U0001F300-\U0001F5FF"
            "|\U0001F680-\U0001F6FF"
            "|\U0001F1E0-\U0001F1FF"
            "|\U00002702-\U000027B0"
            "|\U000024C2-\U0001F251]+"
        )
        return bool(emoji_pattern.search(text))

    def _detect_collage(self, image: Image.Image) -> bool:
        """检测是否为拼图"""
        # 简单的边缘检测来判断是否有明显的分割线
        gray = image.convert('L')
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_pixels = np.array(edges)

        # 检测水平和垂直线
        h_lines = np.mean(edge_pixels, axis=1)
        v_lines = np.mean(edge_pixels, axis=0)

        # 如果有明显的分割线，可能是拼图
        h_peaks = len([x for x in h_lines if x > np.mean(h_lines) + np.std(h_lines)])
        v_peaks = len([x for x in v_lines if x > np.mean(v_lines) + np.std(v_lines)])

        return h_peaks > 2 or v_peaks > 2

    def _detect_border(self, image: Image.Image) -> bool:
        """检测是否有边框"""
        pixels = np.array(image)
        h, w = pixels.shape[:2]

        # 检查边缘像素是否一致
        border_width = 10
        top_edge = pixels[:border_width, :].reshape(-1, 3)
        bottom_edge = pixels[-border_width:, :].reshape(-1, 3)
        left_edge = pixels[:, :border_width].reshape(-1, 3)
        right_edge = pixels[:, -border_width:].reshape(-1, 3)

        # 如果边缘颜色一致性高，可能有边框
        for edge in [top_edge, bottom_edge, left_edge, right_edge]:
            if np.std(edge) < 30:  # 颜色变化小
                return True

        return False

    def _categorize_resolution(self, width: int, height: int) -> str:
        """分类图片分辨率"""
        pixels = width * height
        if pixels < 480 * 480:
            return 'low'
        elif pixels < 1080 * 1080:
            return 'medium'
        else:
            return 'high'

    def _detect_product_image(self, image: Image.Image) -> bool:
        """检测是否为产品图（简化版）"""
        # 产品图通常有较为单一的背景
        pixels = np.array(image)
        bg_color = pixels[0, 0]  # 左上角作为背景参考

        # 计算与背景色相似的像素比例
        diff = np.abs(pixels - bg_color)
        similar_pixels = np.sum(np.max(diff, axis=2) < 30)
        total_pixels = pixels.shape[0] * pixels.shape[1]

        # 如果大部分像素与背景相似，可能是产品图
        return similar_pixels / total_pixels > 0.3

    def _detect_people(self, image: Image.Image) -> bool:
        """检测是否包含人物（简化版）"""
        # 这里使用简化的肤色检测
        pixels = np.array(image)

        # 转换到YCbCr色彩空间进行肤色检测
        # 简化处理：检查是否有大量肤色像素
        r = pixels[:, :, 0]
        g = pixels[:, :, 1]
        b = pixels[:, :, 2]

        # 简单的肤色范围
        skin_mask = (r > 95) & (g > 40) & (b > 20) & \
                   (r > g) & (r > b) & \
                   (np.abs(r - g) > 15)

        skin_ratio = np.sum(skin_mask) / (pixels.shape[0] * pixels.shape[1])

        # 如果肤色像素超过5%，可能包含人物
        return skin_ratio > 0.05

    def _calculate_complexity(self, gray_pixels: np.ndarray) -> str:
        """计算视觉复杂度"""
        # 使用边缘检测的结果来判断复杂度
        edges = np.gradient(gray_pixels)
        edge_density = np.mean(np.abs(edges))

        if edge_density < 10:
            return 'simple'
        elif edge_density < 30:
            return 'medium'
        else:
            return 'complex'

    def _summarize_cover_features(
        self,
        all_features: List[Dict],
        text_contents: List[str],
        color_features: List[Dict],
        layout_features: List[Dict]
    ) -> Dict[str, Any]:
        """
        汇总封面特征

        Returns:
            封面特征汇总
        """
        summary = {
            'total_analyzed': len(all_features),
            'text_analysis': {},
            'color_analysis': {},
            'layout_analysis': {},
            'visual_analysis': {}
        }

        # 文字分析汇总
        if text_contents:
            # 高频词分析
            words = []
            for text in text_contents:
                words.extend(list(jieba.cut(text)))
            word_freq = Counter([w for w in words if len(w) > 1])

            summary['text_analysis'] = {
                'covers_with_text': len([f for f in all_features if f.get('text_content')]),
                'text_coverage_rate': round(
                    len([f for f in all_features if f.get('text_content')]) / len(all_features) * 100, 1
                ),
                'top_keywords': [{'word': w, 'count': c} for w, c in word_freq.most_common(20)],
                'avg_text_length': round(
                    np.mean([len(''.join(f.get('text_content', []))) for f in all_features]), 1
                )
            }

        # 颜色分析汇总
        if color_features:
            color_styles = [f.get('color_style') for f in color_features if f]
            style_counts = Counter(color_styles)

            summary['color_analysis'] = {
                'color_style_distribution': dict(style_counts),
                'avg_brightness': round(
                    np.mean([f.get('avg_brightness', 127) for f in color_features if f]), 1
                ),
                'popular_colors': self._get_popular_colors(color_features)
            }

        # 布局分析汇总
        if layout_features:
            orientations = [f.get('orientation') for f in layout_features if f]
            orientation_counts = Counter(orientations)

            collage_count = sum(1 for f in layout_features if f and f.get('is_collage'))
            border_count = sum(1 for f in layout_features if f and f.get('has_border'))

            summary['layout_analysis'] = {
                'orientation_distribution': dict(orientation_counts),
                'collage_rate': round(collage_count / len(layout_features) * 100, 1),
                'border_rate': round(border_count / len(layout_features) * 100, 1)
            }

        # 视觉元素汇总
        visual_elements = [f.get('visual_elements') for f in all_features if f.get('visual_elements')]
        if visual_elements:
            product_count = sum(1 for v in visual_elements if v.get('is_product_image'))
            people_count = sum(1 for v in visual_elements if v.get('has_people'))

            summary['visual_analysis'] = {
                'product_image_rate': round(product_count / len(visual_elements) * 100, 1),
                'people_image_rate': round(people_count / len(visual_elements) * 100, 1),
                'complexity_distribution': dict(
                    Counter([v.get('visual_complexity') for v in visual_elements])
                )
            }

        return summary

    def _get_popular_colors(self, color_features: List[Dict]) -> List[Dict]:
        """获取流行色"""
        all_colors = []
        for feature in color_features:
            if feature and 'dominant_colors' in feature:
                for color in feature['dominant_colors']:
                    all_colors.append(color['hex'])

        color_counts = Counter(all_colors)
        return [{'color': c, 'count': cnt} for c, cnt in color_counts.most_common(10)]