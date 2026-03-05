"""
TNE-SDK Launcher: Resizable Split Container

A horizontal two-pane layout with a draggable divider.  Drag the thin
handle left or right to resize the panels.  The handle renders as a
single-column bar with ◂ ▸ arrows so it reads as a standard splitter.

Usage:
    with ResizableSplit(initial_left=48, min_left=30, min_right=40):
        yield LeftWidget(id="left")
        yield RightWidget(id="right")

The first child becomes the left pane, the second becomes the right pane.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.events import MouseDown, MouseMove, MouseUp
from textual.widget import Widget
from textual.widgets import Static


class _SplitHandle(Widget):
    """
    Single-column draggable splitter handle.

    Renders a vertical bar with ◂▸ arrows at the vertical center so it
    looks like a standard resizable divider.  Highlights on hover/drag.
    """

    DEFAULT_CSS = """
    _SplitHandle {
        width: 1;
        height: 100%;
        background: #1a1a2e;
        color: #3a3a5e;
        content-align: center middle;
        margin: 0;
        padding: 0;
    }
    _SplitHandle:hover {
        background: #1e2040;
        color: #00d4ff;
    }
    _SplitHandle.-dragging {
        background: #00d4ff 15%;
        color: #00d4ff;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._dragging = False

    def render(self) -> str:
        """Draw a column of thin dots with ◂▸ arrows at the midpoint."""
        h = self.size.height
        if h <= 0:
            return ""
        mid = h // 2
        lines: list[str] = []
        for i in range(h):
            if i == mid - 1:
                lines.append("◂")
            elif i == mid:
                lines.append("┃")
            elif i == mid + 1:
                lines.append("▸")
            else:
                lines.append("│")
        return "\n".join(lines)

    def on_mouse_down(self, event: MouseDown) -> None:
        self._dragging = True
        self.add_class("-dragging")
        self.capture_mouse()
        event.stop()

    def on_mouse_up(self, event: MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.remove_class("-dragging")
            self.release_mouse()
            event.stop()

    def on_mouse_move(self, event: MouseMove) -> None:
        if self._dragging:
            parent = self.parent
            if isinstance(parent, ResizableSplit):
                parent._handle_gutter_drag(event.screen_x)
            event.stop()


class ResizableSplit(Widget):
    """
    Horizontal two-pane container with a draggable divider.

    Parameters
    ----------
    initial_left : int
        Starting width of the left pane in columns.
    min_left : int
        Minimum width the left pane can be dragged to.
    min_right : int
        Minimum width the right pane can be dragged to.
    """

    DEFAULT_CSS = """
    ResizableSplit {
        layout: horizontal;
        width: 100%;
        height: 100%;
    }
    """

    def __init__(
        self,
        initial_left: int = 48,
        min_left: int = 30,
        min_right: int = 40,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._left_width = initial_left
        self._min_left = min_left
        self._min_right = min_right
        self._handle = _SplitHandle()
        self._left_pane: Widget | None = None
        self._right_pane: Widget | None = None

    def compose(self) -> ComposeResult:
        # Children are mounted by the caller via `with ResizableSplit(): yield ...`
        # We intercept them in on_mount.
        return []

    def on_mount(self) -> None:
        """Wrap the two child widgets around the handle."""
        children = list(self.children)
        if len(children) < 2:
            return

        self._left_pane = children[0]
        self._right_pane = children[1]

        # Insert the handle between the two children
        self.mount(self._handle, after=self._left_pane)
        self._apply_widths()

    def _apply_widths(self) -> None:
        """Set CSS widths on the left and right panes."""
        if self._left_pane:
            self._left_pane.styles.width = self._left_width
            self._left_pane.styles.min_width = self._min_left
        if self._right_pane:
            self._right_pane.styles.width = "1fr"

    def _handle_gutter_drag(self, screen_x: int) -> None:
        """Called by the handle on mouse drag. Adjusts the left pane width."""
        region = self.region
        relative_x = screen_x - region.x

        # Clamp to min/max bounds
        max_left = region.width - self._min_right - 1  # 1 for handle
        new_width = max(self._min_left, min(max_left, relative_x))

        if new_width != self._left_width:
            self._left_width = new_width
            self._apply_widths()
