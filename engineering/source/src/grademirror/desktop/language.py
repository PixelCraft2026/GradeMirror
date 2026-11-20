"""Per-user language preference and weak, display-only live text bindings."""
import weakref
from PySide6.QtCore import QObject, QLocale, QSettings, QTranslator, QLibraryInfo, Signal
from PySide6.QtWidgets import QApplication
from .i18n import Text, tr, render, set_language


def resolve_language(preference, locale_name):
    if preference == 'zh_CN':return 'zh_CN'
    if preference == 'en':return 'en'
    return 'zh_CN' if locale_name.lower().replace('-','_').split('_')[0]=='zh' else 'en'


def system_ui_locale():
    locale=QLocale.system()
    languages=locale.uiLanguages()
    return languages[0] if languages else locale.name()


class LanguageManager(QObject):
    changed = Signal()

    def __init__(self, app, *, settings=None, system_locale=None):
        super().__init__(app)
        self.app = app
        from ..light.runtime import application, data_root
        if settings is None:
            settings=QSettings(str(data_root()/"ui.ini"),QSettings.IniFormat) if application() else QSettings('GradeMirror','Desktop')
        self.settings=settings
        self.system_locale = system_locale or system_ui_locale
        value = self.settings.value('ui/language','system')
        self.preference = value if value in ('system','zh_CN','en') else 'system'
        self.language = resolve_language(self.preference,self.system_locale())
        self.bindings = weakref.WeakKeyDictionary()
        self.qt_translator = None
        self._apply_locale()

    def _apply_locale(self):
        set_language(self.language)
        if self.qt_translator is not None:
            self.app.removeTranslator(self.qt_translator)
            self.qt_translator.deleteLater()
            self.qt_translator = None
        if self.language == 'zh_CN':
            translator = QTranslator(self)
            path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if translator.load('qtbase_zh_CN',path):
                self.app.installTranslator(translator);self.qt_translator=translator
            else:translator.deleteLater()

    def set_preference(self, preference, *, persist=True):
        if preference not in ('system','zh_CN','en'):raise ValueError('Unsupported UI language')
        language = resolve_language(preference,self.system_locale())
        if persist:
            previous = self.settings.value('ui/language',None)
            self.settings.setValue('ui/language',preference);self.settings.sync()
            if self.settings.status()!=QSettings.Status.NoError:
                if previous is None:self.settings.remove('ui/language')
                else:self.settings.setValue('ui/language',previous)
                raise OSError('Unable to save language preference')
        self.preference,self.language = preference,language
        self._apply_locale()
        for obj,entries in list(self.bindings.items()):
            for (method,prefix),text in list(entries.items()):
                try:getattr(obj,method)(*prefix,render(text))
                except RuntimeError:
                    self.bindings.pop(obj,None);break
        self.changed.emit()


def manager():
    app=QApplication.instance()
    if app is None:raise RuntimeError('A QApplication is required for UI bindings')
    if not hasattr(app,'_gm_language'):
        app._gm_language=LanguageManager(app)
    return app._gm_language


def set_text(obj, value, method='setText', *prefix):
    owner=manager();key=(method,prefix)
    if isinstance(value,Text):owner.bindings.setdefault(obj,{})[key]=value
    elif obj in owner.bindings:
        owner.bindings[obj].pop(key,None)
    getattr(obj,method)(*prefix,render(value))
    return obj


def text_widget(cls, text, *args, **kwargs):
    return set_text(cls(str(text),*args,**kwargs),text)


def unbind_text(obj, method='setText'):
    owner=manager()
    if obj in owner.bindings:owner.bindings[obj].pop((method,()),None)


def bound_text(obj, method='setText'):
    return manager().bindings.get(obj,{}).get((method,()),getattr(obj,'text',lambda:'')())
