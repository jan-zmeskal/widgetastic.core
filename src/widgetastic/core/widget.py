# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""This module contains the base classes that are used to implement the more specific behaviour."""

import inspect
from smartloc import Locator
from threading import Lock
from wait_for import wait_for

from .browser import Browser
from .exceptions import NoSuchElementException, LocatorNotImplemented


class WidgetDescriptor(object):
    """This class handles instantiating and caching of the widgets on view.

    It stores the class and the parameters it should be instantiated with. Once it is accessed from
    the instance of the class where it was defined on, it passes the instance to the widget class
    followed by args and then kwargs.

    It also acts as a counter, so you can then order the widgets by their "creation" stamp.
    """
    _seq_cnt = 0
    _seq_cnt_lock = Lock()

    def __new__(cls, *args, **kwargs):
        o = super(WidgetDescriptor, cls).__new__(cls)
        with WidgetDescriptor._seq_cnt_lock:
            o._seq_id = WidgetDescriptor._seq_cnt
            WidgetDescriptor._seq_cnt += 1
        return o

    def __init__(self, klass, *args, **kwargs):
        self.klass = klass
        self.args = args
        self.kwargs = kwargs

    def __get__(self, obj, type=None):
        if obj is None:  # class access
            return self

        # Cache on WidgetDescriptor
        if self not in obj._widget_cache:
            obj._widget_cache[self] = self.klass(obj, *self.args, **self.kwargs)
        return obj._widget_cache[self]

    def __repr__(self):
        if self.args:
            args = ', ' + ', '.join(repr(arg) for arg in self.args)
        else:
            args = ''
        if self.kwargs:
            kwargs = ', ' + ', '.join(
                '{}={}'.format(k, repr(v)) for k, v in self.kwargs.iteritems())
        else:
            kwargs = ''
        return '{}({}{}{})'.format(type(self).__name__, self.klass.__name__, args, kwargs)


class Widget(object):
    """Base class for all UI objects.

    Does couple of things:

        * Ensures it gets instantiated with a browser or another widget as parent. If you create an
          instance in a class, it then creates a WidgetDescriptor which is then invoked on the
          instance and instantiates the widget with underlying browser.
        * Implements some basic interface for all widgets.
    """

    def __new__(cls, *args, **kwargs):
        """Implement some typing saving magic.

        Unless you are passing a :py:class:`Widget` or :py:class:`widgetastic.core.browser.Browser`
        as a first argument which implies the instantiation of an actual widget, it will return
        :py:class:`WidgetDescriptor` instead which will resolve automatically inside of
        :py:class:`View` instance.

        This allows you a sort of Django-ish access to the defined widgets then.
        """
        if args and isinstance(args[0], (Widget, Browser)):
            return super(Widget, cls).__new__(cls, *args, **kwargs)
        else:
            return WidgetDescriptor(cls, *args, **kwargs)

    def __init__(self, parent):
        """If you are inheriting from this class, you **MUST ALWAYS** ensure that the inherited class
        has an init that always takes the ``parent`` as the first argument. You can do that on your
        own, setting the parent as ``self.parent`` or you can do something like this:

        .. code-block:: python

            def __init__(self, parent, arg1, arg2):
                super(MyClass, self).__init__(parent)
                # or if you have somehow complex inheritance ...
                Widget.__init__(self, parent)
        """
        self.parent = parent

    @property
    def browser(self):
        """Returns the instance of parent browser.

        Returns:
            :py:class:`widgetastic.core.browser.Browser` instance

        Raises:
            :py:class:`ValueError` when the browser is not defined, which is an error.
        """
        try:
            return self.parent.browser
        except AttributeError:
            raise ValueError('Unknown value {!r} specified as parent.'.format(self.parent))

    @property
    def parent_view(self):
        """Returns a parent view, if the widget lives inside one.

        Returns:
            :py:class:`View` instance if the widget is defined in one, otherwise ``None``.
        """
        if isinstance(self.parent, View):
            return self.parent
        else:
            return None

    @property
    def is_displayed(self):
        """Shortcut allowing you to detect if the widget is displayed.

        If the logic behind is_displayed is more complex, you can always override this.

        Returns:
            :py:class:`bool`
        """
        return self.browser.is_displayed(self)

    def wait_displayed(self, timeout='10s'):
        """Wait for the element to be displayed. Uses the :py:meth:`is_displayed`

        Args:
            timout: If you want, you can override the default timeout here
        """
        wait_for(lambda: self.is_displayed, timeout=timeout, delay=0.2)

    def move_to(self):
        """Moves the mouse to the Selenium WebElement that is resolved by this widget.

        Returns:
            :py:class:`selenium.webdriver.remote.webelement.WebElement` instance
        """
        return self.browser.move_to_element(self)

    def fill(self):
        """Interactive objects like inputs, selects, checkboxes, et cetera should implement fill.

        When you implement this method, it *MUST ALWAYS* return a boolean whether the value
        *was changed*. Otherwise it can break.

        Returns:
            A boolean whether it changed the value or not.
        """
        raise NotImplementedError(
            'Widget {} does not implement fill()!'.format(type(self).__name__))

    def read(self):
        """Each object should implement read so it is easy to get the value of such object.

        When you implement this method, the exact return value is up to you but it *MUST* be
        consistent with what :py:meth:`fill` takes.
        """
        raise NotImplementedError(
            'Widget {} does not implement read()!'.format(type(self).__name__))

    def __element__(self):
        """Default functionality, resolves :py:meth:`__locator__`.

        Returns:
            :py:class:`selenium.webdriver.remote.webelement.WebElement` instance
        """
        try:
            return self.browser.element(self)
        except AttributeError:
            raise LocatorNotImplemented('You have to implement __locator__ or __element__')


def _gen_locator_meth(loc):
    def __locator__(self):  # noqa
        return loc
    return __locator__


class ViewMetaclass(type):
    """metaclass that ensures nested widgets' functionality from the declaration point of view.

    When you pass a ``ROOT`` class attribute, it is used to generate a ``__locator__`` method on
    the view that ensures the view is resolvable.
    """
    def __new__(cls, name, bases, attrs):
        new_attrs = {}
        for key, value in attrs.iteritems():
            if inspect.isclass(value) and getattr(value, '__metaclass__', None) is cls:
                new_attrs[key] = WidgetDescriptor(value)
            else:
                new_attrs[key] = value
        if 'ROOT' in new_attrs:
            # For handling the root locator of the View
            rl = Locator(new_attrs['ROOT'])
            new_attrs['__locator__'] = _gen_locator_meth(rl)
        return super(ViewMetaclass, cls).__new__(cls, name, bases, new_attrs)


class View(Widget):
    """View is a kind of abstract widget that can hold another widgets. Remembers the order,
    so therefore it can function like a form with defined filling order.

    It looks like this:

    .. code-block:: python

        class Login(View):
            user = SomeInputWidget('user')
            password = SomeInputWidget('pass')
            login = SomeButtonWidget('Log In')

            def a_method(self):
                do_something()

    The view is usually instantiated with an instance of
    :py:class:`widgetastic.core.browser.Browser`, which will then enable resolving of all of the
    widgets defined.

    Args:
        parent: A parent :py:class:`View` or :py:class:`widgetastic.core.browser.Browser`
        additional_context: If the view needs some context, for example - you want to check that
            you are on the page of user XYZ but you can also be on the page for user FOO, then
            you shall use the ``additional_context`` to pass in required variables that will allow
            you to detect this.
    """
    __metaclass__ = ViewMetaclass

    def __init__(self, parent, additional_context=None):
        super(View, self).__init__(parent)
        self.context = additional_context or {}
        self._widget_cache = {}

    def flush_widget_cache(self):
        # Recursively ...
        for view in self._views:
            view._widget_cache.clear()
        self._widget_cache.clear()

    @classmethod
    def widget_names(cls):
        """Returns a list of widget names in the order they were defined on the class.

        Returns:
            A :py:class:`list` of :py:class:`Widget` instances.
        """
        result = []
        for key in dir(cls):
            value = getattr(cls, key)
            if isinstance(value, WidgetDescriptor):
                result.append((key, value))
        return [name for name, _ in sorted(result, key=lambda pair: pair[1]._seq_id)]

    @property
    def _views(self):
        """Returns all sub-views of this view.

        Returns:
            A :py:class:`list` of :py:class:`View`
        """
        return [view for view in self if isinstance(view, View)]

    @property
    def is_displayed(self):
        """Overrides the :py:meth:`Widget.is_displayed`. The difference is that if the view does
        not have the root locator, it assumes it is displayed.

        Returns:
            :py:class:`bool`
        """
        try:
            return super(View, self).is_displayed
        except LocatorNotImplemented:
            return True

    def move_to(self):
        """Overrides the :py:meth:`Widget.move_to`. The difference is that if the view does
        not have the root locator, it returns None.

        Returns:
            :py:class:`selenium.webdriver.remote.webelement.WebElement` instance or ``None``.
        """
        try:
            return super(View, self).move_to()
        except LocatorNotImplemented:
            return None

    def fill(self, values):
        """Implementation of form filling.

        This method goes through all widgets defined on this view one by one and calls their
        ``fill`` methods appropriately.

        Args:
            values: A dictionary of ``widget_name: value_to_fill``.

        Returns:
            :py:class:`bool` if the fill changed any value.
        """
        widget_names = self.widget_names()
        was_change = False
        self.before_fill(values)
        for name, value in values.iteritems():
            if name not in widget_names:
                raise NameError('View {} does not have widget {}'.format(type(self).__name__, name))
            if value is None:
                continue

            widget = getattr(self, name)
            try:
                if widget.fill(value):
                    was_change = True
            except NotImplementedError:
                continue

        self.after_fill(was_change)
        return was_change

    def read(self):
        """Reads the contents of the view and presents them as a dictionary.

        Returns:
            A :py:class:`dict` of ``widget_name: widget_read_value`` where the values are retrieved
            using the :py:meth:`Widget.read`.
        """
        result = {}
        for widget_name in self.widget_names():
            widget = getattr(self, widget_name)
            try:
                value = widget.read()
            except (NotImplementedError, NoSuchElementException):
                continue

            result[widget_name] = value

        return result

    def before_fill(self, values):
        """A hook invoked before the loop of filling is invoked.

        Args:
            values: The same values that are passed to :py:meth:`fill`
        """
        pass

    def after_fill(self, was_change):
        """A hook invoked after all the widgets were filled.

        Args:
            was_change: :py:class:`bool` signalizing whether the :py:meth:`fill` changed anything,
        """
        pass

    def __iter__(self):
        """Allows iterating over the widgets on the view."""
        for widget_attr in self.widget_names():
            yield getattr(self, widget_attr)