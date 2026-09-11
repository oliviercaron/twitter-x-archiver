"""Offline validation of dependencies in a frozen companion application."""
import io
import json


def main():
    import httpx
    import yaml
    import curl_cffi
    import twscrape
    import pyarrow as pa
    import pyarrow.parquet as pq
    import imagehash
    from PIL import Image
    image=Image.new('RGB',(32,32),'green')
    assert len(str(imagehash.phash(image)))==16
    assert len(str(imagehash.dhash(image)))==16
    stream=io.BytesIO()
    pq.write_table(pa.table({'id':['1234567890123456789']}),stream)
    stream.seek(0)
    assert pq.read_table(stream).to_pylist()==[{'id':'1234567890123456789'}]
    assert yaml.safe_load('manual: true')['manual'] is True
    print(json.dumps({'ok':True,'checks':['imports','image_phash','image_dhash','parquet_roundtrip','yaml']}))
    return 0

if __name__=='__main__':raise SystemExit(main())
