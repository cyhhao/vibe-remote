"""Shared handler foundation for controller-owned handlers."""

from modules.im import MessageContext
from vibe.i18n import t as i18n_t


class BaseHandler:
    """Provide shared controller references and common helper methods."""

    def __init__(self, controller):
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.settings_manager = controller.settings_manager
        self.sessions = (
            getattr(controller, "sessions", None)
            or getattr(controller.settings_manager, "sessions", None)
            or controller.settings_manager
        )
        self.formatter = getattr(controller.im_client, "formatter", None)

    def _get_settings_key(self, context: MessageContext) -> str:
        return self.controller._get_settings_key(context)

    def _get_lang(self) -> str:
        if hasattr(self.controller, "_get_lang"):
            return self.controller._get_lang()
        return getattr(self.config, "language", "en")

    def _t(self, key: str, **kwargs) -> str:
        return i18n_t(key, self._get_lang(), **kwargs)
