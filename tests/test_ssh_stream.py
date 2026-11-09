import sys
import pytest
from opc_da_bridge.management.ssh_stream import JsonLineProcess, StreamError


def process(body, **kwargs):
    return JsonLineProcess([sys.executable, "-u", "-c", body], **kwargs)


ECHO = '''import sys,json,os
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({'request_id':r['request_id'],'reply':{'ok':True,'pid':os.getpid(),'value':r['payload']}}),flush=True)
'''


def test_reuses_process_and_rotates_with_bounded_requests():
    p = process(ECHO, max_requests=2)
    try:
        a, ta = p.request({"text": "açúcar"})
        b, tb = p.request(False)
        c, tc = p.request(7)
        assert a["pid"] == b["pid"] != c["pid"]
        assert (ta["reused"], tb["reused"], tc["reused"]) == (False, True, False)
        assert a["value"] == {"text": "açúcar"} and b["value"] is False
    finally:
        p.close()


@pytest.mark.parametrize("body", [
    "import sys;sys.stdin.readline();print('invalid',flush=True)",
    "import sys;sys.stdin.readline();print('{\"request_id\":\"wrong\",\"reply\":{}}',flush=True)",
    "import sys;sys.stdin.readline()",
    "import sys,time;sys.stdin.readline();time.sleep(5)",
])
def test_errors_close_owned_process_and_never_retry(body):
    p = process(body, timeout=.1)
    with pytest.raises(StreamError):
        p.request({"operation": "sample"})
    assert p.process is None


def test_large_request_rejected_before_start():
    p = process(ECHO)
    with pytest.raises(StreamError):
        p.request("x" * p.LIMIT)
    assert p.process is None


def test_concurrent_requests_are_single_flight_and_preserve_correlation():
    from concurrent.futures import ThreadPoolExecutor
    p = process(ECHO)
    try:
        with ThreadPoolExecutor(4) as pool:
            responses = list(pool.map(lambda value: p.request(value)[0], range(20)))
        assert [r["value"] for r in responses] == list(range(20))
        assert len({r["pid"] for r in responses}) == 1
    finally:
        p.close()


def test_oversized_response_closes_transport():
    p = process("import sys;sys.stdin.readline();sys.stdout.write('x'*2097153);sys.stdout.flush()")
    with pytest.raises(StreamError):
        p.request({})
    assert p.process is None
