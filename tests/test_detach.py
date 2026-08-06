"""Popping the console into its own window.

The bug this covers: the window opened, correctly sized and still
connected, and rendered nothing. Reparenting a widget hides it - Qt says
so plainly - and the earlier check asserted the *window* was visible
without ever asking whether the console inside it was.
"""

from __future__ import annotations

import pytest

from vmmanager.pages.detail import DetailPage


@pytest.fixture
def page(qapp):
    p = DetailPage()
    p.show()
    qapp.processEvents()
    yield p
    p.shutdown()


def test_the_console_is_visible_in_its_detached_window(page, qapp):
    page._detach_console()
    qapp.processEvents()
    client = page._detached_client
    assert page._detached is not None
    assert page._detached.isVisible(), "the window itself never appeared"
    assert client.isVisible(), "the window is there but the console is hidden"
    assert client.parent() is page._detached


def test_detaching_twice_raises_the_window_it_already_made(page, qapp):
    page._detach_console()
    qapp.processEvents()
    first = page._detached
    page._detach_console()
    qapp.processEvents()
    assert page._detached is first, "a second window was made"
    assert first.isVisible()


def test_closing_the_window_puts_the_console_back(page, qapp):
    before = page.console_stack.count()
    page._detach_console()
    qapp.processEvents()
    client = page._detached_client
    assert page.console_stack.count() == before - 1

    page._detached.close()
    qapp.processEvents()
    assert page._detached is None, "the page still thinks it is detached"
    assert page.console_stack.count() == before
    assert page.console_stack.currentWidget() is client
    # isVisible() is False while the console tab is not the one on screen -
    # that is its parent being hidden, not the console. isHidden() is the
    # question worth asking: was it left explicitly hidden by the reparent?
    assert not client.isHidden(), "back in the stack but explicitly hidden"

    # and on the path someone actually takes: look at the console tab
    page.tabs.setCurrentIndex(page.TAB_CONSOLE)
    qapp.processEvents()
    assert client.isVisible(), "the console tab came back empty"
