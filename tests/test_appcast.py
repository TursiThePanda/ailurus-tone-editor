"""The two-platform appcast: each updater must find exactly its own file."""
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from appcast_add_windows import add_windows  # noqa: E402

SP = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">
    <channel>
        <title>Ailurus Tone Editor</title>
        <item>
            <title>0.3.0</title>
            <pubDate>Sat, 14 Nov 2026 12:00:00 +0100</pubDate>
            <sparkle:version>0.3.0.20</sparkle:version>
            <sparkle:shortVersionString>0.3.0</sparkle:shortVersionString>
            <sparkle:minimumSystemVersion>13.0</sparkle:minimumSystemVersion>
            <sparkle:hardwareRequirements>arm64</sparkle:hardwareRequirements>
            <description><![CDATA[<p>Now on Windows too</p>]]></description>
            <enclosure url="https://x/Ailurus-Tone-Editor-0.3.0.dmg" length="54000000"
                type="application/octet-stream" sparkle:edSignature="MAC=="/>
        </item>
    </channel>
</rss>"""


def test_both_platforms_get_their_own_item():
    out = add_windows(FEED, "https://x/setup.exe", 'sparkle:edSignature="WIN==" length="33000000"')
    items = ET.fromstring(out).findall("./channel/item")
    assert len(items) == 2
    mac, win = (i.find("enclosure") for i in items)
    assert mac.get(f"{SP}os") == "macos" and mac.get(f"{SP}edSignature") == "MAC=="
    assert win.get(f"{SP}os") == "windows-x64" and win.get("url") == "https://x/setup.exe"
    assert win.get(f"{SP}edSignature") == "WIN==" and win.get("length") == "33000000"
    assert items[1].find(f"{SP}version").text == "0.3.0.20"
    assert items[1].find(f"{SP}minimumSystemVersion") is None      # 13.0 would block Windows 10/11
    assert items[1].find("description") is not None                 # release notes in both
    assert items[1].find(f"{SP}hardwareRequirements") is None


def test_signature_without_quotes():
    out = add_windows(FEED, "https://x/s.exe", "sparkle:edSignature=WIN== length=5")
    assert 'sparkle:edSignature="WIN==" length="5"' in out
