"""Pure presentation preferences; no project, task, image or result ownership."""
from dataclasses import dataclass
import math

OBJECTS = {
    'prepare': ('source', 'reference'),
    'result': ('result',),
    'source_result': ('source', 'result'),
    'reference_result': ('reference', 'result'),
    'material_source': ('source',),
    'material_reference': ('reference',),
}
LAYOUTS = ('horizontal', 'vertical')


def _point(value):
    if (isinstance(value, (tuple, list)) and len(value) == 2
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 210_000_000 for n in value)):
        return tuple(value)
    return None


@dataclass
class DisplayState:
    object: str = 'prepare'
    prepare_layout: str = 'horizontal'
    compare_layout: str = 'horizontal'
    sidebar: bool = True
    native_preferred: bool = False
    center: tuple | None = None
    native_center: tuple | None = None
    return_object: str = 'prepare'

    @property
    def roles(self):
        return OBJECTS[self.object]

    @property
    def detail_capable(self):
        return self.object in ('result', 'source_result', 'material_source')

    @property
    def native(self):
        return self.detail_capable and self.native_preferred

    @property
    def temporary(self):
        return self.object.startswith('material_')

    @property
    def layout(self):
        preparing = self.object == 'prepare' or self.temporary and self.return_object == 'prepare'
        return self.prepare_layout if preparing else self.compare_layout

    def select(self, selection):
        if selection not in OBJECTS:
            raise ValueError('Unknown photo object')
        self.object = selection

    def inspect(self, role):
        if role not in ('source', 'reference'):
            raise ValueError('Unknown material role')
        if not self.temporary:
            self.return_object = self.object
        self.object = 'material_' + role

    def return_to_workspace(self):
        if self.temporary:
            self.object = self.return_object

    def arrange(self, layout):
        if layout not in LAYOUTS:
            raise ValueError('Unknown comparison layout')
        if self.object == 'prepare' or self.temporary and self.return_object == 'prepare':
            self.prepare_layout = layout
        else:
            self.compare_layout = layout

    def remember_center(self, x, y):
        point = _point((x, y))
        if point is not None:
            self.center = self.native_center = point

    def clear_document(self):
        self.object = self.return_object = 'prepare'
        self.native_preferred = False
        self.center = self.native_center = None

    def to_record(self):
        return dict(object=self.object, prepare_layout=self.prepare_layout,
                    compare_layout=self.compare_layout, sidebar=self.sidebar,
                    native_preferred=self.native_preferred, center=self.center,
                    native_center=self.native_center, return_object=self.return_object)

    @classmethod
    def from_record(cls, record):
        state = cls()
        if not isinstance(record, dict):
            return state
        for field in ('object', 'return_object'):
            value = record.get(field)
            if isinstance(value, str) and value in OBJECTS:
                setattr(state, field, value)
        for field in ('prepare_layout', 'compare_layout'):
            if record.get(field) in LAYOUTS:
                setattr(state, field, record[field])
        for field in ('sidebar', 'native_preferred'):
            if type(record.get(field)) is bool:
                setattr(state, field, record[field])
        state.center = _point(record.get('center'))
        state.native_center = _point(record.get('native_center'))
        if state.return_object.startswith('material_'):
            state.return_object = 'prepare'
        return state
