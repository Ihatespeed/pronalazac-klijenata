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
    """Vrati True ako lead prolazi filtere."""
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
    """
    Pretražuje Google za Instagram profil firme.
    Vraća URL ili prazan string.
    """
    query = f'"{firm_name}" {location} site:instagram.com'
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": 3,
    }
    try:
        data = requests.get(SERPAPI_URL, params=params, headers=HEADERS, timeout=30).json()
        organic = data.get("organic_results", [])
        for result in organic:
            link = result.get("link", "")
            if "instagram.com/" in link:
                # Filtriraj opšte stranice, uzmi samo profile
                path = link.replace("https://www.instagram.com/", "").replace("https://instagram.com/", "")
                # Preskoči tagove, lokacije, explore
                if path and not path.startswith(("explore/", "p/", "reel/", "tags/", "locations/")):
                    return link
    except Exception:
        pass
    return ""


def enrich_with_instagram(leads, location, api_key, status_text, progress_bar):
    """Dodaje Instagram kolonu na postojeću listu leadova."""
    total = len(leads)
    for i, lead in enumerate(leads):
        name = lead.get("Naziv", "")
        status_text.markdown(f"📸 *Tražim Instagram za: {name}* ({i+1}/{total})")
        progress_bar.progress((i + 1) / total)
        ig = find_instagram_serpapi(name, location, api_key)
        lead["Instagram"] = ig
        # Pauza da ne bombardujemo API
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
            status_text.markdown(f"⏭️ *Preskačem: {name}*")
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
                status_text.markdown(f"⏭️ *Preskačem: {name}*")
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
            status_text.markdown(f"⏭️ *Preskačem: {name}*")
            continue
        seen.add(name.lower())
        leads.append(lead)
        status_text.markdown(f"✅ *Pronađeno: {name}*")

    progress_bar.progress(1.0)
    return leads, None


# ============================================
# IZGLED APLIKACIJE (MODERN UI)
# ============================================
st.set_page_config(page_title="Pronalazač Klijenata", page_icon="🎯", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp {
        background: radial-gradient(circle at 20% 20%, #1a1c2e 0%, #0E1117 50%);
    }
    .hero { text-align: center; padding: 2.5rem 1rem 1rem 1rem; }
    .main-title {
        font-size: 3.5rem; font-weight: 800; letter-spacing: -1px;
        background: linear-gradient(90deg, #6366F1, #8B5CF6, #EC4899);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin: 0;
    }
    .subtitle { color: #9CA3AF; font-size: 1.15rem; margin-top: 0.5rem; }
    .glass-card {
        background: rgba(31, 41, 55, 0.5);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px; padding: 1.5rem; margin-bottom: 1rem;
    }
    .stButton>button {
        background: linear-gradient(90deg, #6366F1, #8B5CF6);
        color: white; border: none; padding: 0.85rem 2rem;
        border-radius: 14px; font-weight: 700; font-size: 1.1rem;
        width: 100%; transition: 0.3s; letter-spacing: 0.3px;
    }
    .stButton>button:hover {
        transform: translateY(-3px);
        box-shadow: 0 12px 30px rgba(139, 92, 246, 0.45);
    }
    .stTextInput>div>div>input,
    .stSelectbox>div>div,
    .stNumberInput>div>div>input {
        background-color: rgba(31, 41, 55, 0.8) !important;
        color: white !important;
        border-radius: 12px !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
    }
    [data-testid="stMetric"] {
        background: rgba(31, 41, 55, 0.6);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px; padding: 1rem;
    }
    [data-testid="stMetricValue"] {
        background: linear-gradient(90deg, #6366F1, #EC4899);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        font-weight: 800;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        background: rgba(31,41,55,0.5); border-radius: 12px 12px 0 0;
        padding: 8px 20px; color: #9CA3AF;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(90deg, #6366F1, #8B5CF6) !important;
        color: white !important;
    }
    #MainMenu, footer, header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <p class="main-title">🎯 Pronalazač Klijenata</p>
    <p class="subtitle">Pronađite firme bez veb sajta — OpenStreetMap, SerpAPI ili Outscraper</p>
</div>
""", unsafe_allow_html=True)

# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("⚙️ Izvor podataka")
    source = st.radio(
        "Odakle preuzeti podatke?",
        ["🌍 OpenStreetMap (besplatno)", "🔎 SerpAPI (Google)", "🛰️ Outscraper (Google)"],
        help="OSM je besplatan ali manje potpun. SerpAPI/Outscraper daju Google podatke (potreban API ključ)."
    )

    api_key = ""
    timeout = 30
    search_in_name = True
    max_pages = 3
    oc_limit = 100

    if "SerpAPI" in source:
        api_key = st.text_input("🔑 SerpAPI ključ", type="password", placeholder="Zalepite ključ")
        st.markdown("[Nabavi ključ →](https://serpapi.com/manage-api-key)")
        max_pages = st.slider("Broj stranica (×20 rezultata)", 1, 10, 3)
    elif "Outscraper" in source:
        api_key = st.text_input("🔑 Outscraper ključ", type="password", placeholder="Zalepite ključ")
        st.markdown("[Nabavi ključ →](https://app.outscraper.cloud/profile)")
        oc_limit = st.slider("Limit rezultata", 20, 400, 100, 20)
    else:
        st.success("✅ Ne treba API ključ!")
        timeout = st.slider("Timeout upita (s)", 10, 120, 30, 5)
        search_in_name = st.checkbox("Pretraži i po imenu (custom)", value=True)

    st.markdown("### 🎚️ Filteri")
    only_without_website = st.checkbox("Samo BEZ sajta", value=True)
    only_with_phone = st.checkbox("Samo sa telefonom", value=False)
    only_with_address = st.checkbox("Samo sa adresom", value=False)
    max_results = st.slider("Maks. rezultata za prikaz", 10, 1000, 200, 10)

    # ---- INSTAGRAM OPCIJA ----
    st.markdown("### 📸 Instagram pretraga")
    find_instagram = st.checkbox(
        "Traži Instagram profil",
        value=False,
        help="Za svaku firmu pretražuje Google (site:instagram.com). Koristi SerpAPI — 1 upit po firmi."
    )

    ig_api_key = ""
    if find_instagram:
        # Ako je već SerpAPI izvor, reusi isti ključ — inače traži poseban
        if "SerpAPI" in source and api_key:
            ig_api_key = api_key
            st.info("📌 Koristi isti SerpAPI ključ.")
        else:
            ig_api_key = st.text_input(
                "🔑 SerpAPI ključ (za Instagram)",
                type="password",
                placeholder="Potreban za Instagram pretragu",
                key="ig_key"
            )
            st.markdown("[Nabavi ključ →](https://serpapi.com/manage-api-key)")
        st.caption(
            "⚠️ Svaka firma = 1 SerpAPI upit. "
            "Pri 200 firmi = 200 upita. Proverite kvotu na [serpapi.com](https://serpapi.com/manage-api-key)."
        )

    st.markdown("---")
    with st.expander("📖 Uputstvo"):
        st.markdown("""
        1. Izaberite **izvor** podataka
        2. (Ako treba) unesite **API ključ**
        3. Izaberite **tip firme** i **lokaciju**
        4. Podesite **filtere**
        5. Opciono: uključite **Instagram pretragu**
        6. Kliknite **Pretraži**
        """)

# ---------- GLAVNI UNOS ----------
c1, c2 = st.columns([1, 1])
with c1:
    preset_choice = st.selectbox("🏢 Tip firme", list(BUSINESS_PRESETS.keys()))
with c2:
    location = st.text_input("📍 Grad / Lokacija", placeholder="npr. Beograd")

custom_text = ""
if preset_choice == "Ostalo / Custom (slobodan tekst)":
    custom_text = st.text_input("✏️ Ključna reč", placeholder="npr. pizza, salon, auto")

search_clicked = st.button("🔍 Pretraži")

# ---------- LOGIKA ----------
if search_clicked:
    osm_filters, default_query = BUSINESS_PRESETS.get(preset_choice, ([], ""))
    query_text = custom_text if custom_text else default_query

    valid = True
    if not location:
        st.warning("⚠️ Unesite lokaciju.")
        valid = False
    elif preset_choice == "Ostalo / Custom (slobodan tekst)" and not custom_text:
        st.warning("⚠️ Unesite ključnu reč za custom pretragu.")
        valid = False
    elif "OpenStreetMap" not in source and not api_key:
        st.warning("⚠️ Unesite API ključ za izabrani izvor.")
        valid = False
    elif find_instagram and not ig_api_key:
        st.warning("⚠️ Unesite SerpAPI ključ za Instagram pretragu.")
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

        progress = st.progress(0)
        status = st.empty()

        # --- KORAK 1: Pronađi firme ---
        with st.spinner("Pretražujem firme..."):
            if "OpenStreetMap" in source:
                leads, display_name = find_leads_osm(settings, progress, status)
            elif "SerpAPI" in source:
                leads, display_name = find_leads_serpapi(settings, progress, status)
            else:
                leads, display_name = find_leads_outscraper(settings, progress, status)

        status.empty()
        progress.empty()

        # --- KORAK 2: Instagram pretraga (opcionalno) ---
        if leads and find_instagram and ig_api_key:
            leads = leads[:max_results]  # Ograniči pre Instagram pretrage
            st.info(f"📸 Tražim Instagram za {len(leads)} firmi... Ovo može potrajati.")
            ig_progress = st.progress(0)
            ig_status = st.empty()
            leads = enrich_with_instagram(leads, location, ig_api_key, ig_status, ig_progress)
            ig_progress.empty()
            ig_status.empty()

        if leads:
            leads = leads[:max_results]
            df = pd.DataFrame(leads)

            # Osiguraj da Instagram kolona postoji
            if "Instagram" not in df.columns:
                df["Instagram"] = ""

            if display_name:
                st.caption(f"📍 Lokacija: {display_name}")

            # Metrike
            cols = st.columns(5 if find_instagram else 4)
            cols[0].metric("Ukupno", len(df))
            cols[1].metric("Sa telefonom", int((df["Telefon"] != "").sum()))
            cols[2].metric("Sa adresom", int((df["Adresa"] != "").sum()))
            cols[3].metric("Bez sajta", int((df["Sajt"] == "").sum()))
            if find_instagram:
                cols[4].metric("Sa Instagramom", int((df["Instagram"] != "").sum()))

            st.success(f"✨ Pronađeno {len(df)} firmi!")

            # Clickable Instagram linkovi u tabeli
            display_df = df.copy()
            if find_instagram and "Instagram" in display_df.columns:
                display_df["Instagram"] = display_df["Instagram"].apply(
                    lambda x: f'<a href="{x}" target="_blank">📸 Otvori</a>' if x else ""
                )

            tab1, tab2 = st.tabs(["📋 Tabela", "🗺️ Mapa"])
            with tab1:
                if find_instagram:
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

            csv = df.to_csv(index=False).encode("utf-8-sig")
            fname = f"klijenti_{preset_choice}_{location}.csv".replace(" ", "_")
            st.download_button("📥 Preuzmi CSV", data=csv, file_name=fname, mime="text/csv")
        else:
            st.info("Nije pronađena nijedna firma. Probajte drugi izvor, tip, grad ili olabavite filtere.")

st.markdown("---")
st.markdown('<p style="text-align:center; color:#6B7280;">Napravljeno sa ❤️</p>', unsafe_allow_html=True)
