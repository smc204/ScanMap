# standard libraries
import gettext
import logging
import numpy as np

_ = gettext.gettext
# _HardwareSource_hardware_source.acquire_data_elements()
class HackerPanelDelegate(object):


    def __init__(self, api):
        self.__api = api
        self.panel_id = 'Hacker-Panel'
        self.panel_name = _('Hacker')
        self.panel_positions = ['left', 'right']
        self.panel_position = 'right'
        #self.adocument_controller = None
        self.input_field = None
        self.send_button = None
        self.api=api
        self.history = []
        self.current_position = 0
        self.locals = locals()
        self.globals = globals()
#        globals()['self'] = self

    def create_panel_widget(self, ui, document_controller):
        self.ui = ui
        self.document_controller = document_controller
        def send_button_clicked(text=None):
            if self.input_field.text:

                self.history.append(self.input_field.text)
                self.current_position = 0
                logging.info('>>> ' + self.input_field.text)
                try:
                    try:
                        result = eval(self.input_field.text, self.globals, self.locals)
                    except SyntaxError:
                        result = exec(self.input_field.text, self.globals, self.locals)
                except Exception as detail:
                    logging.error(str(detail))
                    raise
                finally:
                    self.input_field.text = ''

                if result is not None:
                    logging.info(result)

        def back_button_clicked():
            if len(self.history) > np.abs(self.current_position):
                self.current_position -= 1
                self.input_field.text = self.history[self.current_position]

        def forward_button_clicked():
            if self.current_position < -1:
                self.current_position += 1
                self.input_field.text = self.history[self.current_position]
            else:
                self.current_position = 0
                self.input_field.text = ''


#        def key_pressed(key):
#            print(key)
#
#        self.ui._UserInterface__ui.on_key_pressed = key_pressed
        column = ui.create_column_widget()
        description = ui.create_label_widget('Sends commands to python.')
        self.input_field = ui.create_line_edit_widget()
        self.input_field._LineEditWidget__line_edit_widget.on_return_pressed = send_button_clicked
        #self.input_field.on_editing_finished = send_button_clicked
        #self.back_button = ui.create_push_button_widget('<')
        self.back_button = PushButtonWidget(ui._ui, text='<', properties={"stylesheet": "background-color: '#e5446d'"})
        self.back_button.on_clicked = back_button_clicked
        self.forward_button = ui.create_push_button_widget('>')
        #properties={"stylesheet": "background-color: '#ABABAB'"}
        #self.forward_button._widget._Widget__behavior.properties = properties
        #self.forward_button._widget._Widget__behavior.update_properties()
        self.forward_button.on_clicked = forward_button_clicked

        description_row = ui.create_row_widget()
        checkbox_row = ui.create_row_widget()
        button_row = ui.create_row_widget()

        description_row.add(description)
        description_row.add_stretch()
        checkbox_row.add(self.input_field)
        button_row.add(self.back_button)
        button_row.add_spacing(15)
        button_row.add(self.forward_button)

        column.add(description_row)
        column.add_spacing(10)
        column.add(checkbox_row)
        column.add_spacing(10)
        column.add(button_row)
        column.add_stretch()
        self.column = column
        return column

class PushButtonWidget:

    def __init__(self, ui, text=None, properties=None):
        self.__ui = ui
        self.__push_button_widget = self.__ui.create_push_button_widget(text=text, properties=properties)

    @property
    def _widget(self):
        return self.__push_button_widget

    @property
    def text(self):
        return self.__push_button_widget.text

    @text.setter
    def text(self, value):
        self.__push_button_widget.text = value

    @property
    def on_clicked(self):
        return self.__push_button_widget.on_clicked

    @on_clicked.setter
    def on_clicked(self, value):
        self.__push_button_widget.on_clicked = value

class HackerExtension(object):
    extension_id = 'univie.swifthacker'

    def __init__(self, api_broker):
        api = api_broker.get_api(version='1', ui_version='1')
        self.__panel_ref = api.create_panel(HackerPanelDelegate(api))

    def close(self):
        self.__panel_ref.close()
        self.__panel_ref = None