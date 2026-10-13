'''Edge-TTS 端点兼容层（2026-10 微软端点迁移）。

背景
----
2026-10-10 起，微软把 Edge 浏览器「大声朗读」的 WebSocket 合成端点从

    wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=<TOKEN>

迁移到

    wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key=<TOKEN>

鉴权参数名由 TrustedClientToken 变为 Ocp-Apim-Subscription-Key（取值不变），旧端点对新
握手一律返回 403。当前最新的 edge-tts 7.2.8 尚未跟进，因此这里在运行期把 edge_tts 的
WSS_URL 指向新端点，失败时自动回退旧端点。

Sec-MS-GEC 版本保持旧值 1-143.0.3650.75 即可（新端点仍接受按旧算法生成的令牌）；
微软新增的 v2 令牌（形如 <16hex>-<64hex>，版本 2-155.0.4283.45）无法用旧算法伪造，
本层暂不实现，等到新端点也开始强制校验 v2 时再补。

参考：https://github.com/rany2/edge-tts/issues/490
'''
from __future__ import annotations

import logging
import threading

_LOGGER = logging.getLogger('VoiceSpeaker')

_TRUSTED_CLIENT_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4'

# 新端点：需要 Ocp-Apim-Subscription-Key 参数名（值仍是同一个客户端令牌）
NEW_WSS_URL = (
    'wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1'
    '?Ocp-Apim-Subscription-Key=' + _TRUSTED_CLIENT_TOKEN
)
# 旧端点：2026-10 起对新建连接返回 403，仅作回退
LEGACY_WSS_URL = (
    'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1'
    '?TrustedClientToken=' + _TRUSTED_CLIENT_TOKEN
)

_CANDIDATES = (NEW_WSS_URL, LEGACY_WSS_URL)

_lock = threading.Lock()
_preferred = 0


def _short(url: str) -> str:
    return url.split('?', 1)[0]


def _apply(url: str) -> None:
    '''把 WSS_URL 写入 edge_tts 各模块的全局命名空间。

    edge_tts.communicate 在导入时以 from .constants import WSS_URL 绑定了自己的
    副本，因此必须同时改写两处，只改 constants 不会生效。
    '''
    import edge_tts.communicate as _communicate
    _communicate.WSS_URL = url
    try:
        import edge_tts.constants as _constants
        _constants.WSS_URL = url
    except Exception:  # pragma: no cover - 版本差异时忽略
        pass


def ensure_endpoint() -> str:
    '''确保 edge_tts 指向当前优选端点，返回所用 URL。幂等，可反复调用。'''
    url = _CANDIDATES[_preferred]
    _apply(url)
    return url


def _set_preferred(index: int) -> None:
    global _preferred
    with _lock:
        if _preferred != index:
            _LOGGER.info('Edge-TTS 端点已切换为 %s', _short(_CANDIDATES[index]))
        _preferred = index


def _preferred_order():
    with _lock:
        first = _preferred
    return [first] + [i for i in range(len(_CANDIDATES)) if i != first]


async def save(text: str, voice: str, path: str, **kwargs) -> str:
    '''异步合成并写入 path，端点失败时自动切换另一个端点重试一次。

    返回写入的路径。两个端点都失败时抛出最后一个异常。
    '''
    import edge_tts

    last_exc = None
    for index in _preferred_order():
        url = _CANDIDATES[index]
        _apply(url)
        try:
            await edge_tts.Communicate(text, voice, **kwargs).save(path)
            _set_preferred(index)
            return path
        except Exception as exc:
            last_exc = exc
            _LOGGER.warning(
                'Edge-TTS 端点 %s 失败：%s', _short(url), exc,
            )
    if last_exc is None:  # pragma: no cover - 理论上不可达
        raise RuntimeError('Edge-TTS 没有可用端点')
    raise last_exc


def save_sync(text: str, voice: str, path: str, **kwargs) -> str:
    '''save() 的同步封装：自建事件循环执行一次合成。'''
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(save(text, voice, path, **kwargs))
    finally:
        loop.close()
