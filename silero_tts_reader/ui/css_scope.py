"""Ограничение CSS-правил одним окном.

Провайдер, зарегистрированный через ``add_provider_for_display``, действует на
все окна приложения. Плеер и настройки задают свои цвета через общие селекторы
(``window``, ``button``, ``scale``, ``dropdown``), поэтому правила одного окна
портили вид другого: прозрачный фон плеера делал прозрачным окно настроек, а
синие кнопки настроек перебивали кнопки плеера.

Решение — префикс области в каждом селекторе. Виджет-окно получает класс
(``playback-window``, ``settings-window``), и правило начинает действовать
только внутри него.
"""

from __future__ import annotations

# Селекторы, которые нельзя префиксовать: они задают само окно-область.
_WINDOW_PREFIXES = ("window", ":window")

# Селектор самого окна-области внутри правила. В GTK4 нет узла ``dialog``: окно
# диалога — это ``window`` с классом ``.dialog``, поэтому правило ``dialog {…}``
# не совпадает ни с чем и молча не действует. Писать ``window {…}`` нельзя:
# префикс областей такой селектор не трогает, и правило распространилось бы на
# все окна приложения. Отсюда замена: ``:scope`` превращается в класс окна.
_SELF = ":scope"


def scope_css(css: str, scope: str) -> str:
    """Вернуть CSS, где каждый селектор ограничен классом ``scope``.

    ``scope`` передаётся с точкой: ``".settings-window"``. Селекторы, которые
    сами начинаются с ``window``, остаются как есть — иначе получилось бы
    ``.settings-window window.settings-window``. Селектор ``:scope`` заменяется
    самим классом области: так правится само окно.
    """
    result: list[str] = []
    for line in css.splitlines():
        stripped = line.strip()
        # Правило начинается там, где встречается "{". Тело бывает и на той же
        # строке ("textview, textview text { ... }"), поэтому проверяем
        # вхождение скобки, а не её позицию в конце.
        if "{" in stripped and not stripped.startswith("@"):
            head, _, rest = line.partition("{")
            selectors = [item.strip() for item in head.split(",") if item.strip()]
            scoped: list[str] = []
            for selector in selectors:
                if selector == _SELF:
                    scoped.append(scope)
                elif selector.startswith(_SELF + " "):
                    scoped.append(scope + selector[len(_SELF):])
                elif selector.startswith(_WINDOW_PREFIXES):
                    scoped.append(selector)
                else:
                    scoped.append(f"{scope} {selector}")
            result.append(", ".join(scoped) + " {" + rest)
            continue
        result.append(line)
    return "\n".join(result)
