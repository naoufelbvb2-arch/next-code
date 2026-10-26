"""Private Windows Job for a controlled shell and descendants, assigned suspended.

Only used by the optional execution-control path. This is separate from Desktop's
outer Job, whose independent verification remains mandatory for lease release.
"""
import ctypes as c
from ctypes import wintypes as w
import time
import threading

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

        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.GetCurrentProcess.restype = w.HANDLE
        api.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
        api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.WaitForSingleObject.restype = w.DWORD
        self._processes = {}
        self._tracking_error = False
        self._stop_tracking = threading.Event()
        self._tracker = None

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
        # Hold a creation-bound native handle before the first instruction can
        # spawn descendants. A retained handle prevents PID reuse ambiguity.
        duplicate = w.HANDLE()
        current = self.api.GetCurrentProcess()
        if not self.api.DuplicateHandle(current, int(proc._handle), current, c.byref(duplicate), 0, False, 2):
            raise CleanupUnverified("Tool native handle could not be retained.")
        self._processes[proc.pid] = duplicate.value
        self._tracker = threading.Thread(target=self._track, daemon=True)
        self._tracker.start()
        nt = c.WinDLL("ntdll")
        nt.NtResumeProcess.argtypes = [w.HANDLE]
        nt.NtResumeProcess.restype = c.c_long
        if nt.NtResumeProcess(int(proc._handle)) < 0:
            raise CleanupUnverified("Contained tool could not be resumed.")

    def _capture(self):
        # Bounded native Job membership, not ancestry guesses or recycled PIDs.
        class ProcessList(c.Structure):
            _fields_ = [("assigned", w.DWORD), ("count", w.DWORD),
                        ("pids", c.c_size_t * 1024)]
        members = ProcessList()
        if not self.api.QueryInformationJobObject(self.handle, 3, c.byref(members), c.sizeof(members), None):
            self._tracking_error = True
            return
        if members.assigned > 1024 or members.count > 1024:
            self._tracking_error = True
            return
        for pid in members.pids[:members.count]:
            if pid in self._processes:
                continue
            if len(self._processes) >= 1024:
                self._tracking_error = True
                return
            handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
            if not handle:
                # A vanished PID is NOT an exit certificate. TotalProcesses
                # reconciliation below will detect an unobserved invocation.
                continue
            member = w.BOOL()
            if not self.api.IsProcessInJob(handle, self.handle, c.byref(member)) or not member.value:
                self.api.CloseHandle(handle)
                continue
            self._processes[pid] = handle

    def _track(self):
        try:
            while not self._stop_tracking.is_set():
                self._capture()
                self._stop_tracking.wait(0.005)  # membership polling, no exit inference
        except BaseException:
            self._tracking_error = True

    def stop_and_verify(self):
        if self.handle is None:
            return
        handle = self.handle
        until = time.monotonic() + 5
        try:
            self._stop_tracking.set()
            if self._tracker is not None:
                self._tracker.join(timeout=max(0, until - time.monotonic()))
                if self._tracker.is_alive():
                    raise CleanupUnverified("Tool native tracking has not stopped.")
            self._capture()
            if not self.api.TerminateJobObject(handle, 1):
                raise CleanupUnverified("Tool containment termination was not verified.")
            # Accounting zero can precede process-object signaling. Wait on
            # every retained native process handle within ONE shared deadline.
            for process in self._processes.values():
                remaining = max(0, int((until - time.monotonic()) * 1000))
                if self.api.WaitForSingleObject(process, remaining) != 0:
                    raise CleanupUnverified("Tool process native exit was not verified.")
            accounting = (c.c_uint64 * 6)()
            if not self.api.QueryInformationJobObject(handle, 1, c.byref(accounting), 48, None):
                raise CleanupUnverified("Tool containment accounting unavailable.")
            totals = c.cast(c.byref(accounting, 36), c.POINTER(w.DWORD))
            # A spawn/exit missed by polling, or racing the final snapshot,
            # remains uncertain. Never replace a missing handle with PID loss,
            # active-zero alone, or a best-effort completion-port notification.
            if self._tracking_error or totals[0] != len(self._processes) or totals[1] != 0:
                raise CleanupUnverified("Complete tool-tree native exit evidence unavailable.")
        finally:
            self.api.CloseHandle(handle)  # kill-on-close even on uncertainty
            self.handle = None
            for process in self._processes.values():
                self.api.CloseHandle(process)
            self._processes.clear()
