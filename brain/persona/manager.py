"""线程安全的人格管理器。当前阶段尚未接管 AgentCore。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from typing import Callable

from brain.persona.defaults import build_naga_style_persona
from brain.persona.models import (
    DEFAULT_PERSONA_ID,
    PersonaProfile,
    PersonaSnapshot,
    utc_now_iso,
)
from brain.persona.store import PersonaNotFoundError, PersonaStore, PersonaStoreError


PersonaListener = Callable[[PersonaSnapshot], None]


class PersonaManager:
    """统一管理人格草稿、激活状态和请求级不可变快照。"""

    def __init__(self, store: PersonaStore | None = None):
        self._store = store or PersonaStore()
        self._lock = threading.RLock()
        self._transition_lock = threading.Lock()
        self._listeners: set[PersonaListener] = set()
        self._revision = 0
        self._store.ensure_initialized()
        self._snapshot = self._load_initial_snapshot()

    def _load_initial_snapshot(self) -> PersonaSnapshot:
        state = self._store.read_state()
        active_id = state["active_id"]
        enabled = state["enabled"]
        try:
            profile = self._store.load_profile(active_id)
        except (PersonaNotFoundError, PersonaStoreError, ValueError):
            try:
                profile = self._store.load_profile(DEFAULT_PERSONA_ID)
            except (PersonaNotFoundError, PersonaStoreError, ValueError):
                profile = self._store.restore_default()
            enabled = False
            self._store.write_state({"enabled": False, "active_id": profile.id})
        return PersonaSnapshot(
            profile=profile,
            revision=self._revision,
            enabled=enabled,
            activated_at=utc_now_iso(),
        )

    def get_snapshot(self) -> PersonaSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def enabled(self) -> bool:
        return self.get_snapshot().enabled

    def list_profiles(self) -> list[PersonaProfile]:
        return self._store.list_profiles()

    def load_profile(self, profile_id: str) -> PersonaProfile:
        return self._store.load_profile(profile_id)

    def save_profile(self, profile: PersonaProfile) -> None:
        """只保存草稿；即使编辑的是当前档案，也不会悄悄热应用。"""
        self._store.save_profile(profile)

    def save_and_activate(self, profile: PersonaProfile) -> PersonaSnapshot:
        """原子保存草稿并激活，避免 Web 端出现保存与激活分离造成的旧版本生效。"""
        profile.validate()
        with self._transition_lock:
            self._store.save_profile(profile)
            self._store.write_state({"enabled": True, "active_id": profile.id})
            snapshot, listeners = self._replace_snapshot(profile=profile, enabled=True)
        self._notify(listeners, snapshot)
        return snapshot

    def create_profile(
        self,
        profile_name: str,
        base_id: str = DEFAULT_PERSONA_ID,
        *,
        template: str = "",
    ) -> PersonaProfile:
        base = (
            build_naga_style_persona()
            if template == "naga_style"
            else self._store.load_profile(base_id)
        )
        now = utc_now_iso()
        profile = replace(
            base,
            id=uuid.uuid4().hex,
            profile_name=profile_name.strip(),
            is_builtin=False,
            created_at=now,
            updated_at=now,
        )
        profile.validate()
        self._store.save_profile(profile)
        return profile

    def activate(self, profile_id: str, *, enable: bool = True) -> PersonaSnapshot:
        """先完整校验并持久化状态，再一次性替换内存快照。"""
        with self._transition_lock:
            profile = self._store.load_profile(profile_id)
            profile.validate()
            self._store.write_state({"enabled": enable, "active_id": profile.id})
            snapshot, listeners = self._replace_snapshot(profile=profile, enabled=enable)
        self._notify(listeners, snapshot)
        return snapshot

    def set_enabled(self, enabled: bool) -> PersonaSnapshot:
        with self._transition_lock:
            current = self.get_snapshot()
            # 重新启用时必须从磁盘读取最新草稿，不能复用停用前的旧内存快照。
            profile = self._store.load_profile(current.profile.id)
            profile.validate()
            self._store.write_state({
                "enabled": enabled,
                "active_id": profile.id,
            })
            snapshot, listeners = self._replace_snapshot(
                profile=profile, enabled=bool(enabled)
            )
        self._notify(listeners, snapshot)
        return snapshot

    def restore_default(self, *, activate: bool = False) -> PersonaProfile:
        with self._transition_lock:
            profile = self._store.restore_default()
            current = self.get_snapshot()
            notification = None
            if activate or current.profile.id == DEFAULT_PERSONA_ID:
                enabled = current.enabled if not activate else True
                self._store.write_state({"enabled": enabled, "active_id": profile.id})
                notification = self._replace_snapshot(profile=profile, enabled=enabled)
        if notification is not None:
            snapshot, listeners = notification
            self._notify(listeners, snapshot)
        return profile

    def delete_profile(self, profile_id: str) -> None:
        if self.get_snapshot().profile.id == profile_id:
            raise PersonaStoreError("当前激活的人格不能删除，请先切换到其他人格")
        self._store.delete_profile(profile_id)

    def subscribe(self, listener: PersonaListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(listener)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(listener)

        return unsubscribe

    def _replace_snapshot(
        self, *, profile: PersonaProfile, enabled: bool
    ) -> tuple[PersonaSnapshot, tuple[PersonaListener, ...]]:
        with self._lock:
            self._revision += 1
            snapshot = PersonaSnapshot(
                profile=profile,
                revision=self._revision,
                enabled=enabled,
                activated_at=utc_now_iso(),
            )
            self._snapshot = snapshot
            listeners = tuple(self._listeners)
        return snapshot, listeners

    @staticmethod
    def _notify(listeners: tuple[PersonaListener, ...], snapshot: PersonaSnapshot) -> None:
        # 回调在状态锁和事务锁外执行，允许 UI 或桥接层安全地反向调用管理器。
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                continue


_MANAGER: PersonaManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_persona_manager() -> PersonaManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = PersonaManager()
    return _MANAGER
