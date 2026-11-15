"""Pure event filtering, normalization and bounded per-viewer cooldowns."""
from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from .config import Settings


def clean_text(text: str, limit: int) -> str:
    text = ''.join(c for c in str(text) if not unicodedata.category(c).startswith('C'))
    text = re.sub(r'(?i)\b(?:https?://|www\.)\S+', '', text)
    return ' '.join(text.split())[:limit]


@dataclass(frozen=True)
class Event:
    kind: str
    user_id: str
    name: str
    text: str = ''
    created: float = field(default_factory=time.monotonic)


class Policy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.comments: OrderedDict[str, float] = OrderedDict()
        self.comment_texts: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.joins: OrderedDict[str, float] = OrderedDict()

    def accept(self, event: Event, now: float | None = None) -> tuple[Event | None, str | None]:
        now = time.monotonic() if now is None else now
        if now - event.created > 30:
            return None, 'Evento antiguo descartado.'
        if event.kind not in ('comment', 'join') or not event.user_id:
            return None, 'Evento sin identidad válida descartado.'
        if event.kind == 'comment' and self.settings.mode == 'ignore':
            return None, 'Comentarios desactivados.'
        if event.kind == 'join' and not self.settings.greetings:
            return None, 'Saludos desactivados.'
        normalized = replace(event, name=clean_text(event.name, 60) or 'visitante',
                             text=clean_text(event.text, 300))
        words = [w.strip().casefold() for w in re.split(r'[,;\n]', self.settings.blocked_words)
                 if w.strip()]
        if any(w in f'{normalized.name} {normalized.text}'.casefold() for w in words):
            return None, 'Evento filtrado.'
        if event.kind == 'comment' and not normalized.text:
            return None, 'Comentario vacío descartado.'
        if event.kind == 'comment':
            text_key = (event.user_id, normalized.text.casefold())
            if text_key in self.comment_texts and now - self.comment_texts[text_key] < 60:
                return None, 'Comentario repetido descartado.'
            self.comment_texts[text_key] = now
            self.comment_texts.move_to_end(text_key)
            while self.comment_texts:
                _key, timestamp = next(iter(self.comment_texts.items()))
                if now - timestamp < 60 and len(self.comment_texts) <= 4000:
                    break
                self.comment_texts.popitem(last=False)
        cache, cooldown = ((self.comments, 10) if event.kind == 'comment' else (self.joins, 300))
        if event.user_id in cache and now - cache[event.user_id] < cooldown:
            return None, 'Espera por usuario aplicada.'
        cache[event.user_id] = now
        cache.move_to_end(event.user_id)
        if len(cache) > 2000:
            cache.popitem(last=False)
        return normalized, None

    def greeting(self, event: Event) -> str:
        return clean_text(self.settings.greeting_template.replace('{usuario}', event.name), 400)
