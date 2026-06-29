import streamlit as st
import requests
import pandas as pd
import time

# ============================================
# KONFIGURACIJA
# ============================================
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SERPAPI_URL = "https://serpapi.com/search"
OUTSCRAPER_URL = "https://api.outscraper.cloud/maps/search"

HEADERS = {"User-Agent": "PronalazacKlijenata/3.0"}

BUSINESS_PRESETS = {
    "Frizer": (['"shop"="hairdresser"'], "frizer"),
    "Restoran": (['"amenity"="restaurant"'], "restoran"),
    "Kafić / Kafana": (['"amenity"="cafe"', '"amenity"="bar"', '"amenity"="pub"'], "kafić"),
    "Pekara": (['"shop"="bakery"'], "pekara"),
    "Apoteka": (['"amenity"="pharmacy"'], "apoteka"),
    "Zubar": (['"amenity"="dentist"', '"healthcare"="dentist"'], "zubar"),
    "Auto servis": (['"shop"="car_repair"'], "auto servis"),
    "Teretana": (['"leisure"="fitness_centre"', '"sport"="fitness"'], "teretana"),
    "Kozmetički salon": (['"shop"="beauty"', '"beauty"="salon"'], "kozmetički salon"),
    "Hotel / Smeštaj": (['"tourism"="hotel"', '"tourism"="guest_house"', '"tourism"="apartment"'], "hotel"),
    "Prodavnica (opšte)": (['"shop"'], "prodavnica"),
    "Lekar / Klinika": (['"amenity"="doctors"', '"amenity"="clinic"', '"healthcare"="doctor"'], "lekar"),
    "Advokat": (['"office"="lawyer"'], "advokat"),
    "Cvećara": (['"shop"="florist"'], "cvećara"),
    "Optika": (['"shop"="optician"'], "optika"),
    "Veterinar": (['"amenity"="veterinary"'], "veterinar"),
    "Ostalo / Custom (slobodan tekst)": ([], ""),
}


# ============================================
# OSM: GEOKODIRANJE
# ============================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_location_bbox(location):
    params = {"q": location, "format": "json", "limit": 1}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30).json()
    except Exception:
        return None, None
    if not resp:
        return None, None
    item = resp[0]
    bbox = item["boundingbox"]
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])), item.get("display_name", location)


def build_query(bbox, osm_filters, custom_text, search_in_name, timeout):
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    parts = []
    for f in osm_filters:
        parts.append(f'  node[{f}]{area};')
        parts.append(f'  way[{f}]{area};')
    if custom_text and search_in_name:
        parts.append(f'  node["name"~"{custom_text}",i]{area};')
        parts.append(f'  way["name"~"{custom_text}",i]{area};')
        parts.append(f'  node["shop"~"{custom_text}",i]{area};')
        parts.append(f'  node["amenity"~"{custom_text}",i]{area};')
    body = "\n".join(parts)
    return f"""
    [out:json][timeout:{timeout}];
    (
{body}
    );
    out center tags;
    """


def run_overpass(query):
    last_error = None
    for url in OVERPASS_ENDPOINTS:
        try:
            resp = requests.post(url, data={"data": query}, headers=HEADERS, timeout=90)
            if resp.status_code == 200:
                return resp.json(), None
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
        time.sleep(1)
    return None, last_error


def apply_filters(lead, settings):
    if settings["only_without_website"] and lead["Sajt"]:
        return False
    if settings["only_with_phone"] and not lead["Telefon"]:
        return False
    if settings["only_with_address"] and not lead["Adresa"]:
        return False
    return True


# ============================================
# INSTAGRAM PRETRAGA VIA SERPAPI
# ============================================
def find_instagram_serpapi(firm_name, location, api_key):
    query = f'"{firm_name}" {location} site:instagram.com'
    params = {"engine": "google", "q": query, "api_key": api_key, "num": 3}
    try:
        data = requests.get(SERPAPI_URL, params=params, headers=HEADERS, timeout=30).json()
        for result in data.get("organic_results", []):
            link = result.get("link", "")
            if "instagram.com/" in link:
                path = link.replace("https://www.instagram.com/", "").replace("https://instagram.com/", "")
                if path and not path.startswith(("explore/", "p/", "reel/", "tags/", "locations/")):
                    return link
    except Exception:
        pass
    return ""


def enrich_with_instagram(leads, location, api_key, status_text, progress_bar):
    total = len(leads)
    for i, lead in enumerate(leads):
        name = lead.get("Naziv", "")
        status_text.markdown(f"📸 *Tražim Instagram za: {name}* ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        lead["Instagram"] = find_instagram_serpapi(name, location, api_key)
        time.sleep(0.5)
    progress_bar.progress(1.0)
    status_text.empty()
    return leads


# ============================================
# IZVOR: OPENSTREETMAP
# ============================================
def find_leads_osm(settings, progress_bar, status_text):
    status_text.markdown("📍 *Tražim lokaciju...*")
    bbox, display_name = get_location_bbox(settings["location"])
    if not bbox:
        st.error("Nije pronađena lokacija. Pokušajte drugačije ime grada.")
        return [], None

    status_text.markdown("🔍 *Pretražujem firme preko OpenStreetMap...*")
    query = build_query(bbox, settings["osm_filters"], settings["custom_text"],
                        settings["search_in_name"], settings["timeout"])
    data, err = run_overpass(query)
    if data is None:
        st.error(f"Greška sa svim Overpass serverima: {err}")
        return [], display_name

    elements = data.get("elements", [])
    total = len(elements)
    leads, seen = [], set()

    for i, el in enumerate(elements, 1):
        if total:
            progress_bar.progress(min(i / total, 1.0))
        tags = el.get("tags", {})
        name = tags.get("name", "").strip()
        if not name or name.lower() in seen:
            continue
        website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
        phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile", "")
        email = tags.get("email") or tags.get("contact:email", "")
        street = tags.get("addr:street", "")
        housenumber = tags.get("addr:housenumber", "")
        city = tags.get("addr:city", "")
        postcode = tags.get("addr:postcode", "")
        address = " ".join(filter(None, [street, housenumber, postcode, city]))
        lat = el.get("lat") or el.get("center", {}).get("lat", "")
        lon = el.get("lon") or el.get("center", {}).get("lon", "")
        lead = {
            "Naziv": name,
            "Telefon": phone,
            "Email": email,
            "Adresa": address,
            "Tip": tags.get("shop") or tags.get("amenity") or tags.get("office") or tags.get("tourism", ""),
            "Ocena": "",
            "Sajt": website,
            "Mapa link": f"https://www.google.com/maps?q={lat},{lon}" if lat else "",
        }
        if not apply_filters(lead, settings):
            continue
        seen.add(name.lower())
        leads.append(lead)
        status_text.markdown(f"✅ *Pronađeno: {name}*")

    progress_bar.progress(1.0)
    return leads, display_name


# ============================================
# IZVOR: SERPAPI
# ============================================
def find_leads_serpapi(settings, progress_bar, status_text):
    query = f"{settings['query_text']} {settings['location']}"
    leads, seen = [], set()
    start, max_pages = 0, settings["max_pages"]

    for page in range(max_pages):
        status_text.markdown(f"🔎 *SerpAPI: stranica {page + 1}...*")
        params = {"engine": "google_maps", "q": query, "type": "search",
                  "start": start, "api_key": settings["api_key"]}
        try:
            data = requests.get(SERPAPI_URL, params=params, headers=HEADERS, timeout=60).json()
        except Exception as e:
            st.error(f"Greška (SerpAPI): {e}")
            break
        if "error" in data:
            st.error(f"SerpAPI greška: {data['error']}")
            break
        results = data.get("local_results", [])
        if not results:
            break

        for r in results:
            name = (r.get("title") or "").strip()
            if not name or name.lower() in seen:
                continue
            gps = r.get("gps_coordinates", {})
            lat, lon = gps.get("latitude", ""), gps.get("longitude", "")
            lead = {
                "Naziv": name,
                "Telefon": r.get("phone", ""),
                "Email": "",
                "Adresa": r.get("address", ""),
                "Tip": r.get("type", ""),
                "Ocena": r.get("rating", ""),
                "Sajt": r.get("website", "") or "",
                "Mapa link": f"https://www.google.com/maps?q={lat},{lon}" if lat else "",
            }
            if not apply_filters(lead, settings):
                continue
            seen.add(name.lower())
            leads.append(lead)
            status_text.markdown(f"✅ *Pronađeno: {name}*")

        progress_bar.progress((page + 1) / max_pages)
        start += 20
        if "next" not in data.get("serpapi_pagination", {}):
            break
        time.sleep(1)

    progress_bar.progress(1.0)
    return leads, None


# ============================================
# IZVOR: OUTSCRAPER
# ============================================
def find_leads_outscraper(settings, progress_bar, status_text):
    query = f"{settings['query_text']}, {settings['location']}"
    leads, seen = [], set()
    status_text.markdown("🔎 *Outscraper: šaljem zahtev...*")
    params = {"query": query, "limit": settings["limit"], "async": "false", "language": "sr"}
    api_headers = {**HEADERS, "X-API-KEY": settings["api_key"]}

    try:
        data = requests.get(OUTSCRAPER_URL, params=params, headers=api_headers, timeout=300).json()
    except Exception as e:
        st.error(f"Greška (Outscraper): {e}")
        return [], None
    if isinstance(data, dict) and data.get("status") == "Error":
        st.error(f"Outscraper greška: {data.get('errorMessage', 'Nepoznata greška')}")
        return [], None

    groups = data.get("data", [])
    all_results = []
    for g in groups:
        all_results.extend(g if isinstance(g, list) else [g])
    total = len(all_results)

    for i, r in enumerate(all_results, 1):
        if total:
            progress_bar.progress(min(i / total, 1.0))
        name = (r.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        lat, lon = r.get("latitude", ""), r.get("longitude", "")
        lead = {
            "Naziv": name,
            "Telefon": r.get("phone", ""),
            "Email": r.get("email_1") or "",
            "Adresa": r.get("full_address") or r.get("address", ""),
            "Tip": r.get("type", ""),
            "Ocena": r.get("rating", ""),
            "Sajt": r.get("site") or r.get("website") or "",
            "Mapa link": f"https://www.google.com/maps?q={lat},{lon}" if lat else "",
        }
        if not apply_filters(lead, settings):
            continue
        seen.add(name.lower())
        leads.append(lead)
        status_text.markdown(f"✅ *Pronađeno: {name}*")

    progress_bar.progress(1.0)
    return leads, None


# ============================================
# UI — dotless.co aesthetic
# ============================================
st.set_page_config(page_title="Pronalazač Klijenata", page_icon="🎯", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Reset to light / dotless palette ── */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: #0f0f0f;
}
.stApp {
    background-color: #ffffff;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #fafafa;
    border-right: 1px solid #e8e8e8;
}
[data-testid="stSidebar"] * {
    color: #0f0f0f !important;
}
[data-testid="stSidebar"] .stMarkdown h3 {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #888 !important;
    margin-top: 1.4rem;
    margin-bottom: 0.4rem;
}
[data-testid="stSidebar"] hr {
    border-color: #e8e8e8;
}

/* ── Inputs ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: #fff !important;
    border: 1px solid #d4d4d4 !important;
    border-radius: 8px !important;
    color: #0f0f0f !important;
    font-size: 0.9rem !important;
    padding: 0.55rem 0.75rem !important;
    box-shadow: none !important;
}
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: #0f0f0f !important;
    box-shadow: 0 0 0 2px rgba(15,15,15,0.08) !important;
}

/* Selectbox */
.stSelectbox > div > div {
    background: #fff !important;
    border: 1px solid #d4d4d4 !important;
    border-radius: 8px !important;
    color: #0f0f0f !important;
    font-size: 0.9rem !important;
}

/* Radio */
.stRadio > label { font-size: 0.85rem; color: #444 !important; }

/* Sliders */
.stSlider > div > div > div > div { background: #0f0f0f !important; }

/* Checkboxes */
.stCheckbox > label > div[data-testid="stMarkdownContainer"] p {
    font-size: 0.875rem;
    color: #333 !important;
}

/* ── Primary button → dotless green ── */
.stButton > button {
    background-color: #16a34a !important;
    color: #fff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    padding: 0.6rem 1.4rem !important;
    letter-spacing: 0.01em !important;
    transition: background 0.15s ease !important;
    box-shadow: none !important;
    width: 100%;
}
.stButton > button:hover {
    background-color: #15803d !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ── Download button ── */
[data-testid="stDownloadButton"] > button {
    background-color: #fff !important;
    color: #0f0f0f !important;
    border: 1px solid #d4d4d4 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    padding: 0.5rem 1.2rem !important;
}
[data-testid="stDownloadButton"] > button:hover {
    background-color: #f5f5f5 !important;
    border-color: #aaa !important;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #fff;
    border: 1px solid #e8e8e8;
    border-radius: 10px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricLabel"] p {
    font-size: 0.75rem !important;
    font-weight: 500;
    color: #888 !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
[data-testid="stMetricValue"] {
    color: #0f0f0f !important;
    font-size: 1.7rem !important;
    font-weight: 700 !important;
    -webkit-text-fill-color: #0f0f0f !important;
    background: none !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid #e8e8e8;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    color: #888 !important;
    font-size: 0.875rem;
    font-weight: 500;
    padding: 0.6rem 1.2rem !important;
    margin-bottom: -1px;
}
.stTabs [aria-selected="true"] {
    background: transparent !important;
    border-bottom: 2px solid #0f0f0f !important;
    color: #0f0f0f !important;
}

/* ── Alerts / info ── */
.stInfo { background: #f0fdf4; border-left: 3px solid #16a34a; border-radius: 6px; color: #166534 !important; }
.stSuccess { background: #f0fdf4; border-left: 3px solid #16a34a; border-radius: 6px; }
.stWarning { background: #fffbeb; border-left: 3px solid #d97706; border-radius: 6px; }
.stError { background: #fef2f2; border-left: 3px solid #dc2626; border-radius: 6px; }

/* ── Dataframe ── */
[data-testid="stDataFrame"] { border: 1px solid #e8e8e8; border-radius: 10px; overflow: hidden; }

/* ── Progress bar ── */
.stProgress > div > div > div > div { background-color: #16a34a !important; }

/* ── Divider ── */
hr { border-color: #e8e8e8 !important; }

/* ── Expander ── */
[data-testid="stExpander"] {
    border: 1px solid #e8e8e8 !important;
    border-radius: 8px !important;
    background: #fafafa !important;
}
</style>
""", unsafe_allow_html=True)

# ── HEADER ──────────────────────────────────────────────────────────────────
st.markdown("""
<div style="padding: 2.5rem 0 0.5rem 0; border-bottom: 1px solid #e8e8e8; margin-bottom: 2rem;">
    <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem;">
        <span style="font-size:1.1rem;">🎯</span>
        <span style="font-size:0.85rem; font-weight:600; color:#0f0f0f; letter-spacing:0.02em;">Pronalazač Klijenata</span>
    </div>
    <h1 style="font-size:2.1rem; font-weight:700; color:#0f0f0f; margin:0 0 0.5rem 0; line-height:1.2; letter-spacing:-0.02em;">
        Pronađite firme<br>bez veb sajta.
    </h1>
    <p style="color:#666; font-size:0.95rem; margin:0;">
        OpenStreetMap, SerpAPI ili Outscraper — izaberite izvor i krenite.
    </p>
</div>
""", unsafe_allow_html=True)

# ── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Izvor podataka")
    source = st.radio(
        "Odakle preuzeti podatke?",
        ["🌍 OpenStreetMap (besplatno)", "🔎 SerpAPI (Google)", "🛰️ Outscraper (Google)"],
        help="OSM je besplatan ali manje potpun. SerpAPI/Outscraper daju Google podatke (potreban API ključ).",
        label_visibility="collapsed",
    )

    api_key = ""
    timeout = 30
    search_in_name = True
    max_pages = 3
    oc_limit = 100

    if "SerpAPI" in source:
        api_key = st.text_input("SerpAPI ključ", type="password", placeholder="Zalepite ključ")
        st.markdown("[Nabavi ključ →](https://serpapi.com/manage-api-key)")
        max_pages = st.slider("Broj stranica (×20 rezultata)", 1, 10, 3)
    elif "Outscraper" in source:
        api_key = st.text_input("Outscraper ključ", type="password", placeholder="Zalepite ključ")
        st.markdown("[Nabavi ključ →](https://app.outscraper.cloud/profile)")
        oc_limit = st.slider("Limit rezultata", 20, 400, 100, 20)
    else:
        st.info("Ne treba API ključ.")
        timeout = st.slider("Timeout upita (s)", 10, 120, 30, 5)
        search_in_name = st.checkbox("Pretraži i po imenu (custom)", value=True)

    st.markdown("### Filteri")
    only_without_website = st.checkbox("Samo BEZ sajta", value=True)
    only_with_phone = st.checkbox("Samo sa telefonom", value=False)
    only_with_address = st.checkbox("Samo sa adresom", value=False)
    max_results = st.slider("Maks. rezultata za prikaz", 10, 1000, 200, 10)

    st.markdown("### Instagram pretraga")
    find_instagram = st.checkbox(
        "Traži Instagram profil",
        value=False,
        help="Za svaku firmu pretražuje Google (site:instagram.com). Koristi SerpAPI — 1 upit po firmi.",
    )

    ig_api_key = ""
    if find_instagram:
        if "SerpAPI" in source and api_key:
            ig_api_key = api_key
            st.caption("Koristi isti SerpAPI ključ.")
        else:
            ig_api_key = st.text_input(
                "SerpAPI ključ (za Instagram)",
                type="password",
                placeholder="Potreban za Instagram pretragu",
                key="ig_key",
            )
            st.markdown("[Nabavi ključ →](https://serpapi.com/manage-api-key)")
        st.caption("⚠️ Svaka firma = 1 SerpAPI upit. Pri 200 firmi = 200 upita.")

    st.markdown("---")
    with st.expander("Uputstvo"):
        st.markdown("""
1. Izaberite **izvor** podataka
2. (Ako treba) unesite **API ključ**
3. Izaberite **tip firme** i **lokaciju**
4. Podesite **filtere**
5. Opciono: uključite **Instagram pretragu**
6. Kliknite **Pretraži**
        """)

# ── MAIN SEARCH FORM ─────────────────────────────────────────────────────────
c1, c2 = st.columns([1, 1], gap="medium")
with c1:
    preset_choice = st.selectbox("Tip firme", list(BUSINESS_PRESETS.keys()))
with c2:
    location = st.text_input("Grad / Lokacija", placeholder="npr. Beograd")

custom_text = ""
if preset_choice == "Ostalo / Custom (slobodan tekst)":
    custom_text = st.text_input("Ključna reč", placeholder="npr. pizza, salon, auto")

st.markdown("<div style='margin-top:0.25rem;'></div>", unsafe_allow_html=True)
search_clicked = st.button("Pretraži →")

# ── LOGIC ────────────────────────────────────────────────────────────────────
if search_clicked:
    osm_filters, default_query = BUSINESS_PRESETS.get(preset_choice, ([], ""))
    query_text = custom_text if custom_text else default_query

    valid = True
    if not location:
        st.warning("Unesite lokaciju.")
        valid = False
    elif preset_choice == "Ostalo / Custom (slobodan tekst)" and not custom_text:
        st.warning("Unesite ključnu reč za custom pretragu.")
        valid = False
    elif "OpenStreetMap" not in source and not api_key:
        st.warning("Unesite API ključ za izabrani izvor.")
        valid = False
    elif find_instagram and not ig_api_key:
        st.warning("Unesite SerpAPI ključ za Instagram pretragu.")
        valid = False

    if valid:
        settings = {
            "location": location,
            "osm_filters": osm_filters,
            "custom_text": custom_text,
            "query_text": query_text,
            "api_key": api_key,
            "only_without_website": only_without_website,
            "only_with_phone": only_with_phone,
            "only_with_address": only_with_address,
            "timeout": timeout,
            "search_in_name": search_in_name,
            "max_pages": max_pages,
            "limit": oc_limit,
        }

        st.markdown("<div style='margin-top:1.5rem; border-top:1px solid #e8e8e8; padding-top:1.5rem;'></div>", unsafe_allow_html=True)
        progress = st.progress(0)
        status = st.empty()

        with st.spinner("Pretražujem..."):
            if "OpenStreetMap" in source:
                leads, display_name = find_leads_osm(settings, progress, status)
            elif "SerpAPI" in source:
                leads, display_name = find_leads_serpapi(settings, progress, status)
            else:
                leads, display_name = find_leads_outscraper(settings, progress, status)

        status.empty()
        progress.empty()

        if leads and find_instagram and ig_api_key:
            leads = leads[:max_results]
            st.info(f"Tražim Instagram za {len(leads)} firmi…")
            ig_progress = st.progress(0)
            ig_status = st.empty()
            leads = enrich_with_instagram(leads, location, ig_api_key, ig_status, ig_progress)
            ig_progress.empty()
            ig_status.empty()

        if leads:
            leads = leads[:max_results]
            df = pd.DataFrame(leads)
            if "Instagram" not in df.columns:
                df["Instagram"] = ""

            if display_name:
                st.caption(f"Lokacija: {display_name}")

            # Metrics row
            num_cols = 5 if find_instagram else 4
            cols = st.columns(num_cols, gap="small")
            cols[0].metric("Ukupno", len(df))
            cols[1].metric("Sa telefonom", int((df["Telefon"] != "").sum()))
            cols[2].metric("Sa adresom", int((df["Adresa"] != "").sum()))
            cols[3].metric("Bez sajta", int((df["Sajt"] == "").sum()))
            if find_instagram:
                cols[4].metric("Sa Instagramom", int((df["Instagram"] != "").sum()))

            st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
            st.success(f"Pronađeno {len(df)} firmi.")

            tab1, tab2 = st.tabs(["Tabela", "Mapa"])

            with tab1:
                if find_instagram:
                    display_df = df.copy()
                    display_df["Instagram"] = display_df["Instagram"].apply(
                        lambda x: f'<a href="{x}" target="_blank">↗ Otvori</a>' if x else "—"
                    )
                    st.write(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)
                else:
                    st.dataframe(df, use_container_width=True, height=500)

            with tab2:
                map_df = df.copy()
                coords = map_df["Mapa link"].str.extract(r"q=([-\d.]+),([-\d.]+)")
                map_df["lat"] = pd.to_numeric(coords[0], errors="coerce")
                map_df["lon"] = pd.to_numeric(coords[1], errors="coerce")
                map_df = map_df.dropna(subset=["lat", "lon"])
                if not map_df.empty:
                    st.map(map_df[["lat", "lon"]])
                else:
                    st.info("Nema koordinata za prikaz na mapi.")

            st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
            csv = df.to_csv(index=False).encode("utf-8-sig")
            fname = f"klijenti_{preset_choice}_{location}.csv".replace(" ", "_")
            st.download_button("Preuzmi CSV", data=csv, file_name=fname, mime="text/csv")

        else:
            st.info("Nije pronađena nijedna firma. Probajte drugi izvor, tip, grad ili olabavite filtere.")

# ── FOOTER ───────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:4rem; padding-top:1.5rem; border-top:1px solid #e8e8e8;
            display:flex; justify-content:space-between; align-items:center;">
    <span style="font-size:0.8rem; color:#aaa;">🎯 Pronalazač Klijenata</span>
    <span style="font-size:0.8rem; color:#aaa;">Napravljeno sa ❤️</span>
</div>
""", unsafe_allow_html=True)
