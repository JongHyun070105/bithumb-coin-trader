import os, socket, sys, time, subprocess
secs=float(sys.argv[1]); code=int(sys.argv[2])
print("diag main started pid=%d" % os.getpid(), flush=True)
s=socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
addr=os.environ["NOTIFY_SOCKET"]
s.connect("\0"+addr[1:] if addr.startswith("@") else addr)
s.send(b"READY=1")
print("diag main ready sent", flush=True)
time.sleep(secs)
print("diag main exiting code=%d" % code, flush=True)
sys.exit(code)
