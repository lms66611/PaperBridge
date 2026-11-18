"""Windows HTTPS transport using the OS trust store; no shell or key files."""
import ctypes,io,json,urllib.error,urllib.parse
from ctypes import wintypes as W

def request_json(url,payload=None,timeout=120,headers=None):
    api=ctypes.WinDLL('winhttp',use_last_error=True)
    H=W.HANDLE;P=ctypes.c_void_p;DWORD=W.DWORD
    signatures={
      'WinHttpOpen':([W.LPCWSTR,DWORD,W.LPCWSTR,W.LPCWSTR,DWORD],H),
      'WinHttpConnect':([H,W.LPCWSTR,W.WORD,DWORD],H),
      'WinHttpOpenRequest':([H,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,P,DWORD],H),
      'WinHttpSetTimeouts':([H,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int],W.BOOL),
      'WinHttpSendRequest':([H,W.LPCWSTR,DWORD,P,DWORD,DWORD,ctypes.c_size_t],W.BOOL),
      'WinHttpReceiveResponse':([H,P],W.BOOL),
      'WinHttpQueryHeaders':([H,DWORD,W.LPCWSTR,P,ctypes.POINTER(DWORD),ctypes.POINTER(DWORD)],W.BOOL),
      'WinHttpQueryDataAvailable':([H,ctypes.POINTER(DWORD)],W.BOOL),
      'WinHttpReadData':([H,P,DWORD,ctypes.POINTER(DWORD)],W.BOOL),
      'WinHttpCloseHandle':([H],W.BOOL)}
    for name,(args,result) in signatures.items():
        fn=getattr(api,name);fn.argtypes=args;fn.restype=result
    def check(value):
        if not value:raise OSError(ctypes.get_last_error(),'Windows HTTPS request failed')
        return value
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https':raise ValueError('Windows transport requires HTTPS')
    handles=[]
    try:
        session=check(api.WinHttpOpen('PaperBridge/1.2',4,None,None,0));handles.append(session)
        check(api.WinHttpSetTimeouts(session,int(timeout*1000),int(timeout*1000),int(timeout*1000),int(timeout*1000)))
        conn=check(api.WinHttpConnect(session,parsed.hostname,parsed.port or 443,0));handles.append(conn)
        path=parsed.path or '/'
        if parsed.query:path+='?'+parsed.query
        request=check(api.WinHttpOpenRequest(conn,'POST' if payload is not None else 'GET',path,None,None,None,0x800000));handles.append(request)
        body=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else b''
        buffer=ctypes.create_string_buffer(body) if body else None
        header_text='\r\n'.join(f'{k}: {v}' for k,v in {'Content-Type':'application/json','Accept-Encoding':'identity',**(headers or {})}.items())
        check(api.WinHttpSendRequest(request,header_text,len(header_text),buffer,len(body),len(body),0))
        check(api.WinHttpReceiveResponse(request,None))
        status=DWORD();length=DWORD(ctypes.sizeof(status))
        check(api.WinHttpQueryHeaders(request,19|0x20000000,None,ctypes.byref(status),ctypes.byref(length),None))
        chunks=[]
        while True:
            available=DWORD();check(api.WinHttpQueryDataAvailable(request,ctypes.byref(available)))
            if not available.value:break
            buf=ctypes.create_string_buffer(available.value);read=DWORD()
            check(api.WinHttpReadData(request,buf,available.value,ctypes.byref(read)))
            chunks.append(buf.raw[:read.value])
        raw=b''.join(chunks)
        if status.value>=400:raise urllib.error.HTTPError(url,status.value,'HTTP service error',None,io.BytesIO(raw))
        return json.loads(raw)
    finally:
        for handle in reversed(handles):api.WinHttpCloseHandle(handle)
