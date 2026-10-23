"""Private Windows Job for a controlled shell and descendants, assigned suspended.

Only used by the optional execution-control path. This is separate from Desktop's
outer Job, whose independent verification remains mandatory for lease release.
"""
import ctypes as c
from ctypes import wintypes as w
import time

from kernel.execution import CleanupUnverified


class WindowsToolJob:
    def __init__(self):
        self.api = c.WinDLL("kernel32", use_last_error=True)
        api = self.api
        api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        api.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        api.CloseHandle.argtypes = [w.HANDLE]

        class Basic(c.Structure):
            _fields_ = [("user", c.c_int64), ("job", c.c_int64), ("flags", w.DWORD),
                        ("min", c.c_size_t), ("max", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class Extended(c.Structure):
            _fields_ = [("basic", Basic), ("io", c.c_uint64 * 6),
                        ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                        ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise CleanupUnverified("Tool containment could not be created.")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway
        if not api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            api.CloseHandle(self.handle)
            self.handle = None
            raise CleanupUnverified("Tool containment could not be configured.")

    def assign_and_resume(self, proc):
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise CleanupUnverified("Suspended tool could not be contained.")
        nt = c.WinDLL("ntdll")
        nt.NtResumeProcess.argtypes = [w.HANDLE]
        nt.NtResumeProcess.restype = c.c_long
        if nt.NtResumeProcess(int(proc._handle)) < 0:
            raise CleanupUnverified("Contained tool could not be resumed.")

    def stop_and_verify(self):
        if self.handle is None:
            return
        handle = self.handle
        try:
            if not self.api.TerminateJobObject(handle, 1):
                raise CleanupUnverified("Tool containment termination was not verified.")
            # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: four LARGE_INTEGERs then
            # TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminated.
            accounting = (c.c_uint64 * 6)()
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if not self.api.QueryInformationJobObject(handle, 1, c.byref(accounting), 48, None):
                    raise CleanupUnverified("Tool containment accounting unavailable.")
                if c.cast(c.byref(accounting, 40), c.POINTER(w.DWORD))[0] == 0:
                    return
                time.sleep(0.01)
            raise CleanupUnverified("Tool descendants have not all stopped.")
        finally:
            self.api.CloseHandle(handle)
            self.handle = None
