"""Ordered, bounded connection writes without holding simulation locks."""
from collections import deque
import struct
import threading
import time


class QueuedWriter:
    # Permit the supported 64 MiB join snapshot plus retained input history.
    # The age limit normally disconnects a stalled socket well before this cap.
    def __init__(self,connection,on_failure,*,clock=time.monotonic,
                 max_packets=8192,max_bytes=80*1024*1024,timeout=5.0):
        self.connection=connection;self.on_failure=on_failure;self.clock=clock
        self.max_packets=max_packets;self.max_bytes=max_bytes;self.timeout=timeout
        self.condition=threading.Condition();self.pending=deque();self.size=0
        self.closed=False
        self.thread=threading.Thread(target=self._run,name='wicked-client-writer',daemon=True)
        self.thread.start()

    def send(self,data,*,ping=False):
        size=len(data)+(5 if ping else 0)
        overflow=False
        with self.condition:
            if self.closed:raise OSError('Connection writer is closed')
            if len(self.pending)>=self.max_packets or self.size+size>self.max_bytes:
                overflow=True
            else:
                self.pending.append((data,ping,self.clock(),size));self.size+=size
                self.condition.notify()
        if overflow:
            self.fail('send queue exceeded its limit')
            raise OSError('Connection send queue is full')

    def check_timeout(self):
        with self.condition:
            expired=(not self.closed and self.pending and
                     self.clock()-self.pending[0][2]>=self.timeout)
        if expired:self.fail('oldest queued write exceeded its deadline')

    def fail(self,reason):
        if self.close():self.on_failure(reason)

    def close(self):
        with self.condition:
            if self.closed:return False
            self.closed=True;self.pending.clear();self.size=0
            self.condition.notify_all()
            return True

    def drain(self,timeout=.25):
        # Called only from the departing handler, outside room locks. Give a
        # final rejection/response a chance to leave before closing the socket.
        with self.condition:
            self.condition.wait_for(lambda:self.closed or not self.pending,timeout)

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda:self.closed or self.pending)
                    if self.closed:return
                    data,ping,_,size=self.pending[0]
                if ping:
                    # Stamp at actual write time, after preceding Photon frames.
                    data=b'\xf0'+struct.pack('>I',int(self.clock()*1000)&0xffffffff)+data
                self.connection.sendall(data)
                with self.condition:
                    if self.closed:return
                    self.pending.popleft();self.size-=size
                    self.condition.notify_all()
        except OSError as error:self.fail(str(error))
