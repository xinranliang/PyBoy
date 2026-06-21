#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#

from pyboy.logging.logging cimport Logger
from pyboy.plugins.base_plugin cimport PyBoyPlugin


cdef Logger logger

cdef class RecordReplay(PyBoyPlugin):
    cdef public list recorded_input
    cdef public set current_pressed_buttons
    cdef public int trajectory_step
    cdef public int trajectory_saved_count
    cdef public bint record_trajectory
    cdef public int trajectory_resize
    cdef public object trajectory_output_path

