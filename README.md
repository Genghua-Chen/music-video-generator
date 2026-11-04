# Batch music video generator

With Python 3.10+, FFmpeg (including `ffprobe`), and OpenSSL installed, run this from the project folder:

```sh
./env/bin/python music_video_generator.py
```

For a fresh Python environment, install the renderer dependencies with `python -m pip install numpy pillow pydub`.

The command scans the top level of `raw_music` for NCM and MP3 tracks and writes `unpublished_video/<input filename stem>.mp4` using ASCII hyphens in generated names. Duplicate checks treat full-width hyphens and dashes as equivalent to `-`. A same-named MP4 in `unpublished_video`, `processed_videos`, `processed_video`, the input folder, or the project folder is skipped. If both formats share a filename stem, NCM is tried first. Failed tracks are reported and can be retried on the next run. Use `--input-dir` to scan a different folder.

The renderer reads the title, artist, and embedded artwork from each track. If tags are missing, it uses the filename and recognizes the `Artist - Title` convention. The background defaults to the song cover (`--background-mode cover`). Use `--background-mode random` to choose a random image from `Background` for each track. Use `--background` to supply a fixed image or a different folder to choose from. Missing cover artwork falls back to a same-stem image, a background image, or a plain color. Everything runs locally.

For a single track or to override the inferred details:

```sh
./env/bin/python music_video_generator.py --audio "raw_music/track.mp3" --output "track.mp4"
```

Single-track mode defaults to `unpublished_video/music_video.mp4`. Optional overrides include `--cover`, `--background`, `--song`, and `--artist`. The vinyl sleeve layout is the default. Place a same-stem `.lrc` next to the track to display scrolling lyrics above the progress bar. Use `--style minimal` for the centered cover layout.

---

# ncm2audio

把网易云音乐下载的 `.ncm` 加密文件还原成普通的 MP3 / FLAC，并自动写入歌名、歌手、专辑标签。

> ⚠️ **免责声明**：`.ncm` 是网易云音乐的 DRM 加密格式。本工具仅供对**自己合法下载的歌曲**做个人备份、在其他播放器上收听使用。请勿用于传播或商业用途，否则可能违反网易云音乐用户协议及当地版权法律。

---

## 功能

- 解密 `.ncm`，输出原始音频（MP3 或 FLAC，取决于下载时选的音质）
- 读取文件内嵌的元数据：歌名、歌手、专辑、码率、时长等
- 自动写入音频标签（MP3 → ID3，FLAC → Vorbis Comment）
- 按 `歌手 - 歌名.扩展名` 重命名输出文件
- 支持一次处理多个文件或整个文件夹

## 安装

需要 Python 3.8+。

```bash
pip install pycryptodome mutagen
```

如果需要把 FLAC 转成 MP3，还要装 [ffmpeg](https://ffmpeg.org/)：

```bash
brew install ffmpeg
```

## 使用

转换单个文件：

```bash
python ncm2audio.py "歌曲.ncm"
```

转换整个文件夹（会递归查找其中的 `.ncm`）：

```bash
python ncm2audio.py ~/Music/网易云音乐
```

输出文件放在原文件旁边，例如 `周杰伦 - 晴天.mp3`。

### FLAC 转 MP3（可选）

解出来是 FLAC、但想要 MP3 时：

```bash
ffmpeg -i "歌手 - 歌名.flac" -b:a 320k "歌手 - 歌名.mp3"
```

注意：FLAC 是无损格式，转成 MP3 会有损失，转换后的音质不会比原来的 FLAC 好。

---

## `.ncm` 文件格式

| 顺序 | 内容 | 处理方式 |
|---|---|---|
| 1 | 文件头 `CTENFDAM`（8 字节）+ 2 字节空隙 | 校验 |
| 2 | 密钥长度（4 字节，小端）+ 密钥数据 | 每字节异或 `0x64` → AES-128-ECB 解密（密钥 `hzHRAmso5kInbaxW`）→ 去掉前缀 `neteasecloudmusic`，得到 RC4 密钥 |
| 3 | 元数据长度（4 字节）+ 元数据 | 每字节异或 `0x63` → 去掉前缀 `163 key(Don't modify):` → Base64 解码 → AES-128-ECB 解密（密钥 `#14ljk_!\]&0U<'(`）→ 去掉前缀 `music:` 或 `dj:`，得到 JSON |
| 4 | CRC32（4 字节）+ 版本（1 字节） | 跳过 |
| 5 | 封面帧长度（4 字节）+ 图片长度（4 字节）+ 图片 | 跳过（也可以提取出来作为封面） |
| 6 | 音频数据 | 用 RC4 变种生成 256 字节密钥流，循环逐字节异或 |

### 元数据示例

```json
{
  "musicId": 123456,
  "musicName": "歌曲名",
  "artist": [["歌手A", 1111], ["歌手B", 2222]],
  "album": "专辑名",
  "albumId": 3333,
  "albumPic": "https://p1.music.126.net/xxx.jpg",
  "bitrate": 320000,
  "duration": 245000,
  "format": "mp3",
  "alias": [],
  "transNames": []
}
```

- `artist` 是 `[名字, 歌手ID]` 的列表
- 电台/播客节目（前缀 `dj:`）的歌曲信息放在 `mainMusic` 字段里
- 个别文件的元数据为空，这时输出文件沿用原文件名

---

## 源码：`ncm2audio.py`

```python
#!/usr/bin/env python3
"""把网易云音乐 .ncm 文件还原为 MP3/FLAC，并写入歌名、歌手、专辑标签。"""
import base64
import json
import re
import struct
import sys
from pathlib import Path

from Crypto.Cipher import AES
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError

CORE_KEY = b"hzHRAmso5kInbaxW"
META_KEY = b"#14ljk_!\\]&0U<'("


def unpad(b: bytes) -> bytes:
    return b[: -b[-1]]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()


def parse_meta(raw: bytes) -> dict:
    m = bytes(b ^ 0x63 for b in raw)
    m = base64.b64decode(m[22:])  # 去掉 "163 key(Don't modify):"
    m = unpad(AES.new(META_KEY, AES.MODE_ECB).decrypt(m)).decode("utf-8")
    kind, _, body = m.partition(":")  # "music:{...}" 或 "dj:{...}"
    meta = json.loads(body)
    if kind == "dj":
        meta = meta.get("mainMusic", meta)
    return meta


def build_keystream(key: bytes) -> bytes:
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (box[i] + j + key[i % len(key)]) & 0xFF
        box[i], box[j] = box[j], box[i]
    out = bytearray(256)
    for i in range(256):
        k = (i + 1) & 0xFF
        out[i] = box[(box[k] + box[(box[k] + k) & 0xFF]) & 0xFF]
    return bytes(out)


def write_tags(dst: Path, meta: dict) -> None:
    if not meta:
        return
    tags = {
        "title": meta.get("musicName", ""),
        "artist": "/".join(a[0] for a in meta.get("artist", [])),
        "album": meta.get("album", ""),
    }
    if dst.suffix == ".flac":
        audio = FLAC(dst)
    else:
        try:
            audio = EasyID3(dst)
        except ID3NoHeaderError:
            audio = EasyID3()
    for k, v in tags.items():
        if v:
            audio[k] = v
    audio.save(dst)


def ncm_dump(path: Path) -> Path:
    data = path.read_bytes()
    if data[:8] != b"CTENFDAM":
        raise ValueError("不是 ncm 文件")
    off = 10

    # 1. RC4 密钥
    n = u32(data, off); off += 4
    key = bytes(b ^ 0x64 for b in data[off : off + n]); off += n
    key = unpad(AES.new(CORE_KEY, AES.MODE_ECB).decrypt(key))[17:]  # 去掉 "neteasecloudmusic"

    # 2. 元数据
    n = u32(data, off); off += 4
    meta = parse_meta(data[off : off + n]) if n else {}
    off += n

    # 3. 跳过 CRC、版本、封面
    off += 5
    frame_len = u32(data, off); off += 4
    img_len = u32(data, off); off += 4
    off += max(frame_len, img_len)

    # 4. 解密音频（大整数异或，比逐字节循环快很多）
    audio = data[off:]
    stream = build_keystream(key)
    ks = (stream * (len(audio) // 256 + 1))[: len(audio)]
    out = (int.from_bytes(audio, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(audio), "big")

    # 5. 确定格式和文件名
    fmt = meta.get("format") or ("flac" if out[:4] == b"fLaC" else "mp3")
    title = meta.get("musicName")
    artists = "、".join(a[0] for a in meta.get("artist", []))
    stem = safe_name(f"{artists} - {title}") if title and artists else path.stem
    dst = path.with_name(f"{stem}.{fmt}")

    dst.write_bytes(out)
    write_tags(dst, meta)
    return dst


def iter_inputs(args):
    for a in args:
        p = Path(a).expanduser()
        if p.is_dir():
            yield from sorted(p.rglob("*.ncm"))
        else:
            yield p


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python ncm2audio.py <文件.ncm 或 文件夹> ...")
        sys.exit(1)
    ok = fail = 0
    for p in iter_inputs(sys.argv[1:]):
        try:
            print(f"✓ {p.name} -> {ncm_dump(p).name}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"✗ {p.name}: {e}")
            fail += 1
    print(f"\n完成：成功 {ok}，失败 {fail}")


if __name__ == "__main__":
    main()
```

---

## 常见问题

**解出来是杂音 / 播放器打不开？**
多半是封面段的偏移和你的客户端版本不一致。可以试试把第 3 步改成老格式的写法：`off += 9`，接着读 4 字节图片长度，再跳过图片。

**为什么有的歌是 FLAC？**
下载时选了"无损"或"Hi-Res"音质。`.ncm` 里装的是什么格式，就原样还原成什么格式。

**缓存文件 `.uc` 能用这个转吗？**
不能。`.uc` 是客户端的播放缓存，格式更简单：每个字节异或 `0xA3` 就是原始音频，不需要这个工具。

**会上传我的文件吗？**
不会，所有处理都在本地完成。

## 相关开源项目

- [ncmdump](https://github.com/taurusxin/ncmdump)：C++ 实现的命令行工具
- [Unlock Music](https://git.unlock-music.dev/um/web)：在浏览器本地解密的网页工具，支持多种平台的加密格式
