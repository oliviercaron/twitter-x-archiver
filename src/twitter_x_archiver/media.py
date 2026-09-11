"""Sequential original-media archiving, URL/identity/hash dedup, optional ffprobe."""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
import httpx
from PIL import Image
from .archive import now


def original_image(url):
    if not url:
        return None
    p = urlsplit(url)
    q = parse_qs(p.query)
    if p.hostname == 'pbs.twimg.com':
        q['name'] = ['orig']
    path = p.path.rsplit(':',1)[0] if p.hostname == 'pbs.twimg.com' and ':' in p.path else p.path
    return urlunsplit((p.scheme,p.netloc,path,urlencode(q,doseq=True),''))


def allowed(url):
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname is not None and (p.hostname == 'twimg.com' or p.hostname.endswith('.twimg.com'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def probe(path, executable='ffprobe'):
    binary = shutil.which(executable)
    if not binary:
        return {'validation_status':'ffprobe_unavailable'}
    try:
        p = subprocess.run([binary,'-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,timeout=60,check=True)
        raw = json.loads(p.stdout)
        stream = next(s for s in raw['streams'] if s.get('codec_type')=='video')
        fps = stream.get('avg_frame_rate','0/1').split('/')
        rate = float(fps[0])/float(fps[1]) if len(fps)==2 and float(fps[1]) else None
        return {'validation_status':'valid','actual_duration_seconds':float(raw.get('format',{}).get('duration',stream.get('duration',0))),
                'actual_width':stream.get('width'),'actual_height':stream.get('height'),'actual_codec':stream.get('codec_name'),
                'actual_fps':rate,'actual_file_size':path.stat().st_size,'ffprobe_raw':raw}
    except (subprocess.SubprocessError,ValueError,KeyError,StopIteration,OSError):
        return {'validation_status':'ffprobe_failed'}


class Downloader:
    def __init__(self, store, config, client=None):
        self.store, self.config = store, config
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(90,connect=30),trust_env=False,follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def download(self, url, relative, media_id=None, video=False):
        result = {'source_url':url,'download_success':False,'download_http_status':None,'download_error':None,
                  'local_media_path':None,'file_size_bytes':None,'sha256':None,'download_observed_at':now()}
        if not url:
            return {**result,'download_error':'no_mp4_variant' if video else 'missing_url'}
        if not allowed(url):
            return {**result,'download_error':'unsupported_media_host'}
        keys = ['url:'+url]
        if media_id:
            # Media ID alone must not reuse a low-quality variant or thumbnail as the video.
            keys.append('media:'+str(media_id)+':'+urlsplit(url).path+':'+urlsplit(url).query)
        for key in keys:
            cached = self.store.file(key)
            if cached and cached.get('download_success'):
                p = self.store.root/cached['local_media_path']
                if p.exists() and p.stat().st_size == cached['file_size_bytes']:
                    return {**cached,'source_url':url,'reused':True}
        dest = self.store.root/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        # A sidecar closes the crash window between durable file write and DB commit.
        sidecar = dest.with_suffix(dest.suffix+'.json')
        if sidecar.exists():
            cached = json.loads(sidecar.read_text(encoding='utf-8'))
            existing = self.store.root/cached['local_media_path']
            if cached['source_url']==url and existing.exists() and digest(existing)==cached['sha256']:
                self.store.put_file(keys,cached)
                return {**cached,'reused':True}
        # Preserve previous media if the tweet's source/quality changes.
        if dest.exists():
            dest = dest.with_name(dest.stem+'_'+hashlib.sha256(url.encode()).hexdigest()[:12]+dest.suffix)
            sidecar = dest.with_suffix(dest.suffix+'.json')
            if sidecar.exists():
                cached = json.loads(sidecar.read_text(encoding='utf-8'))
                existing = self.store.root/cached['local_media_path']
                if cached['source_url']==url and existing.exists() and digest(existing)==cached['sha256']:
                    self.store.put_file(keys,cached)
                    return {**cached,'reused':True}
        tmp = dest.with_suffix(dest.suffix+'.partial')
        try:
            current = url
            for redirect in range(6):
                if not allowed(current):
                    return {**result,'download_error':'unsupported_redirect_host'}
                async with self.client.stream('GET',current) as rep:
                    result['download_http_status'] = rep.status_code
                    if rep.status_code in (301,302,303,307,308):
                        current = str(rep.url.join(rep.headers['location']))
                        continue
                    if rep.status_code != 200:
                        return {**result,'download_error':f'http_{rep.status_code}'}
                    h = hashlib.sha256()
                    size = 0
                    with tmp.open('wb') as f:
                        async for chunk in rep.aiter_bytes(1024*1024):
                            f.write(chunk); h.update(chunk); size += len(chunk)
                        f.flush(); os.fsync(f.fileno())
                    expected = rep.headers.get('content-length')
                    if not size or (expected and not rep.headers.get('content-encoding') and size != int(expected)):
                        raise ValueError('incomplete_file')
                    if video:
                        with tmp.open('rb') as f:
                            header = f.read(64)
                        if b'ftyp' not in header:
                            raise ValueError('invalid_mp4')
                        validation = probe(tmp,self.config.get('ffprobe','ffprobe'))
                        if validation['validation_status']=='ffprobe_failed':
                            raise ValueError('ffprobe_failed')
                        result.update(validation)
                    else:
                        with Image.open(tmp) as img:
                            img.verify()
                        with Image.open(tmp) as img:
                            result.update(actual_width=img.width,actual_height=img.height)
                            if self.config.get('image_hashes',True):
                                import imagehash
                                result['phash'] = str(imagehash.phash(img))
                                result['dhash'] = str(imagehash.dhash(img))
                    checksum = h.hexdigest()
                    same = self.store.file('sha256:'+checksum)
                    if same and (self.store.root/same['local_media_path']).exists():
                        # Different unknown URLs require bytes once before SHA comparison is possible.
                        tmp.unlink()
                        local = same['local_media_path']
                    else:
                        os.replace(tmp,dest)
                        local = str(dest.relative_to(self.store.root)).replace('\\','/')
                    result.update(download_success=True,local_media_path=local,file_size_bytes=size,sha256=checksum)
                    from .archive import atomic_json
                    atomic_json(sidecar,result)
                    self.store.put_file(keys+['sha256:'+checksum],result)
                    return result
            return {**result,'download_error':'redirect_limit'}
        except (httpx.HTTPError,OSError,ValueError) as e:
            # Exception text may contain URLs/session data; record only safe class/category.
            result['download_error'] = type(e).__name__
            return result
        finally:
            if tmp.exists():
                tmp.unlink()  # our incomplete download only

    async def enrich(self, row):
        for m in row.get('media',[]):
            video = m['media_type'] in ('video','animated_gif')
            url = m.get('best_video_url') if video else original_image(m.get('media_url'))
            parts = urlsplit(url or '')
            ext = 'mp4' if video else (parse_qs(parts.query).get('format',[None])[0] or Path(parts.path).suffix.lstrip(':.' ) or 'jpg')
            if ext not in ('mp4','jpg','jpeg','png','webp','gif','avif'):
                ext = 'jpg'
            name = f"{m['source_tweet_id']}_{m['media_index']:02d}.{ext}"
            prior = m.get('download')
            fresh = await self.download(url,Path('media')/('videos' if video else 'images')/name,m.get('media_id'),video)
            if prior and prior.get('download_success') and prior.get('source_url') != url:
                m.setdefault('archived_downloads',[]).append(prior)
            m['download'] = fresh
            # Convenient requested fields directly on media as well as full download object.
            for key in ['local_media_path','download_success','download_http_status','download_error','file_size_bytes','sha256']:
                m[key] = fresh.get(key)
            if video and m.get('thumbnail_url'):
                thumb = original_image(m['thumbnail_url'])
                m['thumbnail_download'] = await self.download(thumb,Path('media/thumbnails')/(Path(name).stem+'.jpg'))
            if m.get('duration_ms') is not None and fresh.get('actual_duration_seconds') is not None:
                m['duration_difference_seconds'] = fresh['actual_duration_seconds']-m['duration_ms']/1000
                m['duration_consistent'] = abs(m['duration_difference_seconds']) <= max(1,m['duration_ms']/1000*0.02)
            if not fresh['download_success']:
                self.store.event('media_download_failed',tweet_id=row['tweet_id'],media_index=m['media_index'],error=fresh['download_error'])
            if not fresh.get('reused'):
                await asyncio.sleep(self.config.get('pause_seconds',2))
        self.store.save(row)
        self.store.db.commit()
