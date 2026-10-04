import os, socket, sys, time
secs=float(sys.argv[1]); code=int(sys.argv[2])
print("collector-like INFO main started", flush=True)
s=socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
addr=os.environ["NOTIFY_SOCKET"]
s.connect("\0"+addr[1:] if addr.startswith("@") else addr)
s.send(b"READY=1")
time.sleep(secs)
sys.exit(code)
