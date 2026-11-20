"""Two mutually exclusive, keyboard accessible matching mode buttons."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame,QHBoxLayout,QButtonGroup,QPushButton,QSizePolicy

class ModeSwitch(QFrame):
    currentIndexChanged=Signal(int)
    def __init__(self):
        super().__init__();self.setObjectName('modeSwitch');self._index=0
        self.buttons=[];self.group=QButtonGroup(self);self.group.setExclusive(True)
        row=QHBoxLayout(self);row.setContentsMargins(3,3,3,3);row.setSpacing(2)
        for i in range(2):
            button=QPushButton();button.setObjectName('modeOption');button.setCheckable(True);button.setMinimumHeight(26);button.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
            self.group.addButton(button,i);self.buttons.append(button);row.addWidget(button)
        self.buttons[0].setChecked(True);self.group.idClicked.connect(self.setCurrentIndex)
    def currentData(self):return('fast','fine')[self._index]
    def currentIndex(self):return self._index
    def setItemText(self,index,text):self.buttons[index].setText(text);self.buttons[index].setAccessibleName(text)
    def itemText(self,index):return self.buttons[index].text()
    def setCurrentIndex(self,index):
        if index not in(0,1):return
        self.buttons[index].setChecked(True)
        if self._index!=index:self._index=index;self.currentIndexChanged.emit(index)
