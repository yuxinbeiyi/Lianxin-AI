"""
VoiceSpeaker：Edge-TTS 语音合成 + pygame 播放
使用独立通道播放，避免与背景音乐冲突
"""
import threading
import asyncio
import logging
import os
import re
import tempfile
import time
import edge_tts
import pygame

logger = logging.getLogger("VoiceSpeaker")


class VoiceSpeaker:
    _edge_tts_lock = threading.Lock()
    # pygame.mixer（SDL）非线程安全：多 SpeakerWorker 并发播放/停止是
    # Windows access violation / 0x8001010d 原生崩溃的高发源，统一串行化。
    _pygame_lock = threading.RLock()

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self._voice = voice
        self._stop_flag = False
        self._ready = False
        self._current_channel = None   # 记录当前播放的通道

    def init_player(self):
        """初始化 pygame 播放器"""
        with VoiceSpeaker._pygame_lock:
            if not self._ready:
                if not pygame.get_init():
                    pygame.init()
                pygame.mixer.init()
                self._ready = True

    # ── 文本清洗方法 ─────────────────────────────────
    def _clean_text_for_tts(self, text: str) -> str:
        """安全清洗：移除 Markdown、emoji、特殊符号，使 TTS 不读乱码。
        增强版：处理表格、代码文件名、驼峰拆分、符号替换，让中英混读更自然。
        """
        import re
        if not text:
            return ""

        try:
            # 0. 先删分隔线
            text = re.sub(r'-{3,}|={3,}|~{3,}', '\n', text)

            # 1. Markdown 链接 [text](url) -> text
            text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
            # 2. 图片
            text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', '', text)
            # 3. 加粗斜体等（保留内部文字）
            text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
            text = re.sub(r'\*([^\*]+)\*', r'\1', text)
            text = re.sub(r'__([^_]+)__', r'\1', text)
            text = re.sub(r'~~([^~]+)~~', r'\1', text)
            # 4. 行内代码
            text = re.sub(r'`([^`]+)`', r'\1', text)
            # 5. 移除标题标记（行首的 #）
            text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
            # 6. 移除代码块标记
            text = re.sub(r'```[\s\S]*?```', '', text)
            text = re.sub(r'~~~[\s\S]*?~~~', '', text)

            # 7. 移除所有 emoji（Unicode 表情符号区段）
            text = re.sub(
                r'[\U0001F300-\U0001F9FF]'
                r'|[\U0001FA70-\U0001FAFF]'
                r'|[\U00002702-\U000027B0]'
                r'|[\U0001F1E0-\U0001F1FF]'
                r'|[\U0000FE00-\U0000FE0F]'
                r'|[\U0000200D]'
                r'|[❤️⭐✨💡🔥🎶🎵💤💢💦💨💫🌟]',
                '', text
            )
            text = text.replace('\u200d', '').replace('\ufeff', '').replace('\u200b', '')

            # 8. 颜文字
            text = re.sub(
                r'[\(（\[［][\s\-＝=]*[｀´・ω∀∂⊙◎●○■□△▲▼☆★♪♫♬αβγδεθλμπσφψ]+[\s\-＝=]*[\)）\]］]',
                '', text
            )

            # 9. 符号替换
            text = text.replace('——', '，')
            text = text.replace('–', '，')
            text = text.replace('—', '，')
            text = re.sub(r'(?<=[^\d])-(?=[^\d])', ' ', text)
            text = text.replace('_', ' ')
            text = text.replace('~', ' ')
            text = text.replace('|', '，')
            text = re.sub(r'\\+', ' ', text)
            text = text.replace('^', ' ')
            text = text.replace('@', ' at ')
            text = text.replace('&', ' and ')
            text = text.replace('+', ' plus ')
            text = text.replace('=', ' equals ')
            text = text.replace('#', ' ')
            text = text.replace('/', ' ')
            text = re.sub(r'\$\$?', ' ', text)
            text = re.sub(r'%', ' percent ', text)
            # 箭头
            text = re.sub(r'→|➔|➜', '到', text)
            text = re.sub(r'↘|↙', '', text)
            # 范围
            text = re.sub(r'(\d+)\s*~\s*(\d+)', r'\1 到 \2', text)
            text = re.sub(r'(\d+)\s*~\s*(\d+)', r'\1 到 \2', text)
            # 圆角数字圈
            text = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩]', '', text)

            # 10. 移除 URL
            text = re.sub(r'https?://[^\s,，。！？、\)）】]+', '', text)

            # 11. 规范化重复标点
            text = re.sub(r'[。！？；，]{2,}', lambda m: m.group(0)[0], text)

            # 11b. 英文省略号 → 句号
            text = re.sub(r'\.{3,}', '。', text)

            # 11b2. 省略号/破折号/间隔号 → Edge-TTS 无法朗读，替换为逗号
            text = text.replace('……', '，')
            text = text.replace('…', '，')
            text = text.replace('——', '，')
            text = text.replace('—', '，')
            text = text.replace('·', '，')

            # 11c. 删除所有残留的 Unicode 颜文字/特殊符号
            text = re.sub(
                r'[^\u4e00-\u9fff\u3400-\u4dbf'
                r'a-zA-Z0-9'
                r'\s\n'
                r'。！？，、；：\u201c\u201d\u2018\u2019（）…—…·'
                r'\.\,\!\?\;\:\-\(\)'
                r']+',
                '', text
            )

            # 11d. 删除残留的波浪线
            text = text.replace('~', '')
            text = text.replace('～', '')

            # 11e. 删除残留的特殊符号
            text = re.sub(r'[\^\*\_\`\#\$\%\&\[\]\{\}\<\>]', '', text)

            # 12. 处理驼峰命名和点分隔文件名
            def split_camel_case(match):
                word = match.group(0)
                if len(word) <= 1:
                    return word
                word = re.sub('([a-z0-9])([A-Z])', r'\1 \2', word)
                return word.lower()

            def split_dot(match):
                return match.group(0).replace('.', ' dot ')

            text = re.sub(r'[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+', split_dot, text)
            text = re.sub(r'[A-Z][a-zA-Z]+', split_camel_case, text)

            # 13. 折叠多个空行
            text = re.sub(r'\n{3,}', '\n\n', text)

            # 14. 首尾空白
            return text.strip()

        except Exception as e:
            print(f"[TTS清洗异常] {e}")
            return ""


    # ── 公开接口 ─────────────────────────────────────────────

    def _split_into_sentences(self, text: str, max_len: int = 100, min_len: int = 10):
        """将文本按句末标点或换行分割为句子列表。
        增强版：
        - 过长句子继续按逗号分号拆分
        - 过短句子（小于 min_len）合并，避免停顿太多
        - 单句不超过 max_len，防止 GPT-SoVITS 乱读
        """
        import re
        if not text:
            return []

        # 第一步：按句末标点和换行拆分
        parts = re.split(r'(?<=[。！？.!?；;])\s*|\n+', text)
        parts = [p.strip() for p in parts if p.strip()]

        # 第二步：拆分过长句子
        result = []
        for p in parts:
            if len(p) > max_len:
                # 按逗号分号继续拆分
                subparts = re.split(r'(?<=[，,;；])\s*', p)
                # 如果子部分还是太长，直接按长度切
                for sp in subparts:
                    if len(sp) > max_len:
                        # 按 max_len 切分
                        for i in range(0, len(sp), max_len):
                            result.append(sp[i:i+max_len])
                    else:
                        result.append(sp)
            else:
                result.append(p)

        # 第三步：合并过短句（减少不必要停顿）
        merged = []
        current = ""
        for p in result:
            if not current:
                current = p
            elif len(current) + len(p) <= max_len and len(p) < min_len:
                current += "，" + p
            else:
                merged.append(current)
                current = p
        if current:
            merged.append(current)

        return [p.strip() for p in merged if p.strip()]

    def speak(self, text: str):
        """合成并播放文字（阻塞直到播放完毕）。自动清洗文本，长文本分句合成。"""
        if not text or not text.strip():
            return
        
        cleaned_text = self._clean_text_for_tts(text)
        
        if not cleaned_text or not cleaned_text.strip():
            print("[TTS] 清洗后文本为空，放弃朗读")
            return
        
        self._stop_flag = False
        self.init_player()

        # 分句：每句不超过 100 字，防止 GPT-SoVITS 长文本后半段乱读
        sentences = self._split_into_sentences(cleaned_text)
        
        if not sentences:
            return

        from config import get_tts_config
        tts_cfg = get_tts_config()
        try:
            from brain.tts_engine import TtsEngine
            # Edge-TTS 模式不要初始化旧的 GPT-SoVITS/ffmpeg 引擎。
            _temp = None if tts_cfg.get("engine") == "edge_tts" else TtsEngine()
            engine = _temp if (tts_cfg.get("engine") != "edge_tts" and _temp.gpt_sovits_available) else None

        except Exception as e:
            logger.warning(f"初始化 GPT-SoVITS 失败，将使用 Edge-TTS: {e}")
            engine = None

        # 整段文字统一检测情绪，避免每句随机选不同参考音频导致声音不一致
        from brain.tts_engine import _detect_mood
        configured_mood = tts_cfg.get("default_mood", "auto")
        mood_hint = None if configured_mood == "auto" else configured_mood
        unified_mood = _detect_mood(cleaned_text, mood_hint) or "casual"
        tts_speed = tts_cfg.get("speed", 1.0)
        # ── 单句：快速路径（无流水线开销） ──────────
        if len(sentences) == 1:
            tmp_path = None
            try:
                tmp_path = self._synthesize_sentence(
                    sentences[0], engine, unified_mood, tts_speed
                )
                if tmp_path and not self._stop_flag:
                    self._play(tmp_path)
            finally:
                self._remove_temp_file(tmp_path)
            return

        # ── 多句：流水线合成 + 播放 ──────────────────
        import queue as _queue
        audio_queue = _queue.Queue(maxsize=2)
        temp_files = []
        queue_done = object()

        def _producer():
            for i, sent in enumerate(sentences):
                if self._stop_flag:
                    audio_queue.put(queue_done)
                    return
                if not sent.strip():
                    continue

                tmp_path = self._synthesize_sentence(
                    sent, engine, unified_mood, tts_speed, sequence=i
                )
                if tmp_path:
                    temp_files.append(tmp_path)
                    audio_queue.put(tmp_path)

            audio_queue.put(queue_done)

        prod = threading.Thread(target=_producer, daemon=True)
        prod.start()

        while True:
            tmp_path = audio_queue.get()
            if tmp_path is queue_done:
                break
            if self._stop_flag:
                continue
            self._play(tmp_path)

        prod.join(timeout=3)

        for fp in temp_files:
            self._remove_temp_file(fp)


    def stop(self):
        """停止当前播放。"""
        self._stop_flag = True
        with VoiceSpeaker._pygame_lock:
            if self._current_channel:
                try:
                    self._current_channel.stop()
                except Exception:
                    pass
                self._current_channel = None
            else:
                try:
                    pygame.mixer.stop()
                except Exception:
                    pass

    # ── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _new_temp_path(suffix: str) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        path = tmp.name
        tmp.close()
        return path

    @staticmethod
    def _remove_temp_file(path: str | None):
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    _last_edge_tts_time: float = 0.0
    _edge_tts_min_interval: float = 0.5

    def _synthesize_sentence(self, text: str, engine, mood: str,
                             speed: float, sequence: int | None = None) -> str | None:
        """合成单句并返回可播放文件。

        GPT-SoVITS 原生输出 WAV，避免依赖 FFmpeg；只有 GPT 合成本身失败或
        不可用时，才让 Edge-TTS 直接生成 MP3。
        """
        tag = f"_s{sequence}" if sequence is not None else ""
        if engine and engine.gpt_sovits_available:
            wav_path = self._new_temp_path(f"{tag}.wav")
            if engine.synthesize_gpt_wav(text, wav_path, mood=mood, speed=speed):
                logger.info(f"TTS 使用 GPT-SoVITS（WAV，text_len={len(text)}）")
                return wav_path
            self._remove_temp_file(wav_path)
            logger.warning("GPT-SoVITS 合成失败，回退 Edge-TTS")

        mp3_path = self._new_temp_path(f"{tag}.mp3")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Edge-TTS is a shared network service. Serializing requests avoids
            # intermittent empty responses when several SpeakerWorkers overlap.
            with VoiceSpeaker._edge_tts_lock:
                now = time.monotonic()
                elapsed = now - VoiceSpeaker._last_edge_tts_time
                if elapsed < VoiceSpeaker._edge_tts_min_interval:
                    time.sleep(VoiceSpeaker._edge_tts_min_interval - elapsed)
                if self._stop_flag:
                    return None
                loop.run_until_complete(self._async_synthesize(text, mp3_path))
                if self._stop_flag:
                    return None
                VoiceSpeaker._last_edge_tts_time = time.monotonic()
            logger.info(f"TTS 使用 Edge-TTS（MP3，text_len={len(text)}）")
            return mp3_path
        except Exception as e:
            logger.error(f"Edge-TTS 合成失败（已丢弃分句「{text[:30]}」）: {e}")
            self._remove_temp_file(mp3_path)
            return None
        finally:
            loop.close()

    def _synthesize(self, text: str) -> str | None:
        """兼容旧调用：按当前配置合成一个可直接播放的临时文件。"""
        try:
            from brain.tts_engine import TtsEngine
            from config import get_tts_config
            engine = TtsEngine()
            cfg = get_tts_config()
            selected = engine if cfg.get("engine") != "edge_tts" else None
            mood = cfg.get("default_mood", "auto")
            speed = cfg.get("speed", 1.0)
            return self._synthesize_sentence(text, selected, mood, speed)
        except Exception as e:
            print(f"[TTS合成出错] {e}")
            return None

    async def _async_synthesize(self, text: str, path: str):
        import random
        last_error = None
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                self._remove_temp_file(path)
                communicate = edge_tts.Communicate(text, self._voice)
                await communicate.save(path)
                if not os.path.exists(path) or os.path.getsize(path) < 256:
                    raise RuntimeError("Edge-TTS returned an empty audio file")
                return
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    base_delay = 1.5 * (2 ** attempt)
                    jitter = random.uniform(0, 0.5)
                    delay = base_delay + jitter
                    logger.warning(
                        "Edge-TTS 暂时失败（第 %s/%s 次）：%s；%.1fs 后重试",
                        attempt + 1, max_retries, exc, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_error

    def _play(self, path: str):
        try:
            with VoiceSpeaker._pygame_lock:
                if not pygame.get_init():
                    pygame.init()
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                # 加载为 Sound 对象，避免使用 music 通道
                sound = pygame.mixer.Sound(path)
                from utils.settings import get_settings
                volume = get_settings().tts_volume
                sound.set_volume(volume)
                # 查找空闲通道播放
                channel = pygame.mixer.find_channel()
                if channel is None:
                    # 如果没有空闲通道，直接播放（可能会抢占其他音效，但概率低）
                    sound.play()
                    self._current_channel = None
                else:
                    channel.play(sound)
                    self._current_channel = channel
            # 等待播放结束（不持锁，避免阻塞其他线程的 stop()）
            while True:
                if self._stop_flag:
                    with VoiceSpeaker._pygame_lock:
                        if self._current_channel:
                            try:
                                self._current_channel.stop()
                            except Exception:
                                pass
                        else:
                            pygame.mixer.stop()
                    break
                with VoiceSpeaker._pygame_lock:
                    if self._current_channel:
                        busy = self._current_channel.get_busy()
                    else:
                        busy = pygame.mixer.get_busy()
                if not busy:
                    break
                pygame.time.wait(50)
        except Exception as e:
            print(f"[TTS播放出错] {e}")
        finally:
            with VoiceSpeaker._pygame_lock:
                self._current_channel = None
