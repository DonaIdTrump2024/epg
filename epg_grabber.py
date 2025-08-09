import requests
import xmltodict
import shutil
import os
import gzip
import re
import sys
from datetime import datetime, timedelta

script_dir = os.path.dirname(os.path.abspath(__file__))

M3U_FILE = "playlist.m3u"
EPG_XML_FILE = "epg.xml"
EPG_GZ_FILE = "myepg.xml.gz"
OUTPUT_EPG = "my-epg.xml"


# 1. Download playlist
def download_playlist(url, filepath):
    response = requests.get(url)
    if response.status_code == 200:
        with open(filepath, "wb") as f:
            f.write(response.content)
        print(f"✅ Плейлист сохранён: {filepath}")
    else:
        print(f"❌ Ошибка скачивания плейлиста: {response.status_code}")
        exit()


# 2. Download and extract EPG if needed
def download_epg(url, epg_file):
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(epg_file, "wb") as f:
            f.write(response.content)
        print(f"✅ EPG скачан: {url}")

        with open(epg_file, "rb") as f:
            if f.read(2) == b"\x1f\x8b":
                print("📦 EPG архивирован, распаковка...")
                with gzip.open(epg_file, "rb") as gz:
                    with open(EPG_XML_FILE, "wb") as xml_f:
                        xml_f.write(gz.read())
                print("✅ EPG успешно распакован.")
            else:
                print("✅ EPG не архивирован. Используется как есть.")
                os.rename(epg_file, EPG_XML_FILE)

        try:
            os.remove(epg_file)
        except PermissionError as e:
            print(f"❌ Не удалось удалить {epg_file}: {e}")
    else:
        print(f"❌ Ошибка скачивания EPG: {url}, HTTP Статус: {response.status_code}")
        exit()


# 3. Read M3U playlist and extract tvg-ids & channel names
def get_m3u_data(m3u_file):
    if not os.path.exists(m3u_file):
        print(f"❌ Плейлист не найден: {m3u_file}")
        exit()

    tvg_ids = set()
    channel_names = set()

    with open(m3u_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF"):
                tvg_id_match = re.search(r'tvg-id="([^"]+)"', line, re.IGNORECASE)
                name_match = re.search(r',(.+)$', line)

                if tvg_id_match:
                    tvg_ids.add(tvg_id_match.group(1).strip().lower())

                if name_match:
                    channel_names.add(name_match.group(1).strip().lower())

    print(f"✅ Найдено {len(tvg_ids)} уникальных tvg-id в плейлисте.")
    print(f"✅ Найдено {len(channel_names)} уникальных названий каналов в плейлисте.")
    return tvg_ids, channel_names


# 4. Нормализация display-names
def normalize_display_names(display):
    names = []
    if isinstance(display, list):
        for name in display:
            if isinstance(name, dict):
                names.append(name.get("#text", "").strip().lower())
            else:
                names.append(str(name).strip().lower())
    else:
        if isinstance(display, dict):
            names.append(display.get("#text", "").strip().lower())
        else:
            names.append(str(display).strip().lower())
    return names


# 5. Фильтрация EPG
def filter_epg(m3u_tvg_ids, m3u_channel_names):
    if not os.path.exists(EPG_XML_FILE):
        print("❌ EPG файл не найден.")
        exit()

    with open(EPG_XML_FILE, "r", encoding="utf-8") as f:
        epg_data = xmltodict.parse(f.read())

    if "tv" not in epg_data or "channel" not in epg_data["tv"]:
        print("❌ Некорректный формат EPG.")
        exit()

    epg_channel_map = {}

    for channel in epg_data["tv"]["channel"]:
        channel_id = channel["@id"].strip().lower()
        display_names = normalize_display_names(channel.get("display-name", []))
        epg_channel_map[channel_id] = display_names

    valid_channel_ids = set()
    for channel_id, names in epg_channel_map.items():
        if channel_id in m3u_tvg_ids or any(name in m3u_channel_names for name in names):
            valid_channel_ids.add(channel_id)

    print(f"✅ Совпало {len(valid_channel_ids)} каналов EPG с плейлистом.")

    programmes_within_cutoff = []
    channel_program_count = {}

    now = datetime.utcnow()
    cutoff = now + timedelta(days=2)

    for program in epg_data["tv"].get("programme", []):
        program_channel_id = program["@channel"].strip().lower()
        if program_channel_id in valid_channel_ids:
            start_str = program["@start"]
            match = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", start_str)
            if match:
                start_dt = datetime(
                    year=int(match.group(1)),
                    month=int(match.group(2)),
                    day=int(match.group(3)),
                    hour=int(match.group(4)),
                    minute=int(match.group(5)),
                    second=int(match.group(6))
                )
                if start_dt <= cutoff:
                    programmes_within_cutoff.append(program)
                    channel_program_count[program_channel_id] = channel_program_count.get(program_channel_id, 0) + 1
            else:
                programmes_within_cutoff.append(program)
                channel_program_count[program_channel_id] = channel_program_count.get(program_channel_id, 0) + 1

    filtered_channels = []
    for channel in epg_data["tv"]["channel"]:
        channel_id = channel["@id"].strip().lower()
        if channel_id in valid_channel_ids and channel_program_count.get(channel_id, 0) > 0:
            filtered_channels.append(channel)

    epg_data["tv"]["channel"] = filtered_channels
    epg_data["tv"]["programme"] = programmes_within_cutoff

    with open(OUTPUT_EPG, "w", encoding="utf-8") as f:
        f.write(xmltodict.unparse(epg_data, pretty=True))

    print(f"✅ Отфильтрованный EPG сохранён как {OUTPUT_EPG}")

    with open(OUTPUT_EPG, "rb") as f_in:
        with gzip.open(EPG_GZ_FILE, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    print(f"✅ EPG архивирован как {EPG_GZ_FILE}")

    os.remove(EPG_XML_FILE)
    os.remove(OUTPUT_EPG)


# 🔥 Основной блок
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("❌ Использование: python script.py <PLAYLIST_URL> <EPG_URL1> <EPG_URL2> ...")
        exit()

    playlist_url = sys.argv[1]
    epg_urls = sys.argv[2:]

    download_playlist(playlist_url, M3U_FILE)

    combined_epg = {"tv": {"channel": [], "programme": []}}

    for index, url in enumerate(epg_urls, start=1):
        epg_file = f"epg_{index}.xml.gz"
        download_epg(url, epg_file)

        with open(EPG_XML_FILE, "r", encoding="utf-8") as f:
            epg_data = xmltodict.parse(f.read())

        combined_epg["tv"]["channel"].extend(epg_data["tv"]["channel"])
        combined_epg["tv"]["programme"].extend(epg_data["tv"].get("programme", []))

    with open(EPG_XML_FILE, "w", encoding="utf-8") as f:
        f.write(xmltodict.unparse(combined_epg, pretty=True))

    m3u_tvg_ids, m3u_channel_names = get_m3u_data(M3U_FILE)
    filter_epg(m3u_tvg_ids, m3u_channel_names)
