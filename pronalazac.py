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

HEADERS = {"User-Agent": "PronalazacKlijenata/2.0"}

# Mapiranje "ljudskih" naziva na OSM tagove (proširuje pretragu)
BUSINESS_PRESETS = {
    "Frizer": ['"shop"="hairdresser"'],
    "Restoran": ['"amenity"="restaurant"'],
    "Kafić / Kafana": ['"amenity"="cafe"', '"amenity"="bar"', '"amenity"="pub"'],
    "Pekara": ['"shop"="bakery"'],
    "Apoteka": ['"amenity"="pharmacy"'],
    "Zubar": ['"amenity"="dentist"', '"healthcare"="dentist"'],
    "Auto servis": ['"shop"="car_repair"'],
    "Teretana": ['"leisure"="fitness_centre"', '"sport"="fitness"'],
    "Kozmetički salon": ['"shop"="beauty"', '"beauty"="salon"'],
    "Hotel / Smeštaj": ['"tourism"="hotel"', '"tourism"="guest_house"', '"tourism"="apartment"'],
    "Prodavnica (opšte)": ['"shop"'],
    "Lekar / Klinika": ['"amenity"="doctors"', '"amenity"="clinic"', '"healthcare"="doctor"'],
    "Advokat": ['"office"="lawyer"'],
    "Cvećara": ['"shop"="florist"'],
    "Optika": ['"shop"="optician"'],
    "Veterinar": ['"amenity"="veterinary"'],
    "Ostalo / Custom (slobodan tekst)": [],
}


# ============================================
# GEOKODIRANJE
# ============================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_location_bbox(location):
    """Pronađi geografske granice grada preko Nominatim (sa keširanjem)."""
    params = {"q": location, "format": "json", "limit": 1}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30).json()
    except Exception:
        return None, None
    if not resp:
        return None, None
    item = resp[0]
    bbox = item["boundingbox"]  # [south, north, west, east]
    display_name = item.get("display_name", location)
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])), display_name


# ============================================
# IZGRADNJA UPITA
# ============================================
def build_query(bbox, osm_filters, custom_text, search_in_name, timeout):
    south, north, west, east = bbox
    area = f"({south},{west},{north},{east})"
    parts = []

    # Pretraga po definisanim tagovima (preset)
    for f in osm_filters:
        parts.append(f'  node[{f}]{area};')
        parts.append(f'  way[{f}]{area};')

    # Pretraga po slobodnom tekstu u imenu
    if custom_text and search_in_name:
        parts.append(f'  node["name"~"{custom_text}",i]{area};')
        parts.append(f'  way["name"~"{custom_text}",i]{area};')
        # pokušaj i po generičkim tagovima ako je custom
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
    """Pokušaj više Overpass servera dok jedan ne uspe."""
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


# ============================================
# GLAVNA LOGIKA
# ============================================
def find_leads(settings, progress_bar, status_text):
    status_text.text("📍 Tražim lokaciju...")
    bbox, display_name = get_location_bbox(settings["location"])
    if not bbox:
        st.error("Nije pronađena lokacija. Pokušajte drugačije ime grada.")
        return [], None

    status_text.text("🔍 Gradim upit i pretražujem firme...")
    query = build_query(
        bbox,
        settings["osm_filters"],
        settings["custom_text"],
        settings["search_in_name"],
        settings["timeout"],
    )

    data, err = run_overpass(query)
    if data is None:
        st.error(f"Greška pri konekciji sa svim Overpass serverima: {err}")
        return [], display_name

    elements = data.get("elements", [])
    total = len(elements)
    leads = []
    seen_names = set()

    for i, el in enumerate(elements, 1):
        if total:
            progress_bar.progress(min(i / total, 1.0))

        tags = el.get("tags", {})
        name = tags.get("name", "").strip()

        if not name:
            continue

        # Deduplikacija (po imenu, case-insensitive)
        name_key = name.lower()
        if name_key in seen_names:
            continue

        website = tags.get("website") or tags.get("contact:website") or tags.get("url")
        phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile", "")
        email = tags.get("email") or tags.get("contact:email", "")

        # ---- FILTERI ----
        if settings["only_without_website"] and website:
            status_text.text(f"⏭️ Preskačem (ima sajt): {name}")
            continue

        if settings["only_with_phone"] and not phone:
            continue

        if settings["only_with_address"] and not tags.get("addr:street"):
            continue

        seen_names.add(name_key)

        # Sastavi adresu
        street = tags.get("addr:street", "")
        housenumber = tags.get("addr:housenumber", "")
        city = tags.get("addr:city", "")
        postcode = tags.get("addr:postcode", "")
        address = " ".join(filter(None, [street, housenumber, postcode, city]))

        lat = el.get("lat") or el.get("center", {}).get("lat", "")
        lon = el.get("lon") or el.get("center", {}).get("lon", "")
        maps_link = f"https://www.google.com/maps?q={lat},{lon}" if lat else ""

        lead = {
            "Naziv": name,
            "Telefon": phone,
            "Email": email,
            "Adresa": address,
            "Tip": tags.get("shop") or tags.get("amenity") or tags.get("office") or tags.get("tourism", ""),
            "Sajt": website or "",
            "Mapa link": maps_link,
        }
        leads.append(lead)
        status_text.text(f"✅ Pronađeno: {name}")

    progress_bar.progress(1.0)
    return leads, display_name


# ============================================
# IZGLED APLIKACIJE (GUI)
# ============================================
st.set_page_config(page_title="Pronalazač Klijenata", page_icon="🎯", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #0E1117; }
    .main-title {
        font-size: 3rem; font-weight: 800;
        background: linear-gradient(90deg, #6366F1, #8B5CF6);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        text-align: center; margin-bottom: 0;
    }
    .subtitle {
        text-align: center; color: #9CA3AF;
        font-size: 1.1rem; margin-bottom: 2rem;
    }
    .stButton>button {
        background: linear-gradient(90deg, #6366F1, #8B5CF6);
        color: white; border: none; padding: 0.75rem 2rem;
        border-radius: 12px; font-weight: 600; font-size: 1.1rem;
        width: 100%; transition: 0.3s;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 25px rgba(99, 102, 241, 0.4);
    }
    .stTextInput>div>div>input, .stSelectbox>div>div {
        background-color: #1F2937; color: white;
        border-radius: 10px; border: 1px solid #374151;
    }
    .metric-box {
        background: #1F2937; padding: 1rem; border-radius: 12px;
        text-align: center; border: 1px solid #374151;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-title">🎯 Pronalazač Klijenata</p>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Pronađite firme BEZ veb sajta (OpenStreetMap - besplatno)</p>', unsafe_allow_html=True)

# ---------- SIDEBAR: PODEŠAVANJA ----------
with st.sidebar:
    st.header("⚙️ Podešavanja")
    st.success("✅ Ne treba API ključ! Koristi besplatni OpenStreetMap.")

    st.markdown("### 🎚️ Filteri rezultata")
    only_without_website = st.checkbox("Samo firme BEZ sajta", value=True)
    only_with_phone = st.checkbox("Samo firme sa telefonom", value=False)
    only_with_address = st.checkbox("Samo firme sa adresom", value=False)

    st.markdown("### 🔧 Napredna podešavanja")
    search_in_name = st.checkbox("Pretraži i po imenu (za custom tekst)", value=True)
    timeout = st.slider("Timeout upita (sekunde)", 10, 120, 30, 5)
    max_results = st.slider("Maksimalan broj rezultata za prikaz", 10, 1000, 200, 10)

    st.markdown("---")
    st.markdown("### 📖 Uputstvo")
    st.markdown("""
    1. Izaberite **tip firme** iz liste (ili "Custom")
    2. Unesite **lokaciju** (grad)
    3. Podesite **filtere** po želji
    4. Kliknite **Pretraži**

    ⚠️ OSM podaci su manje potpuni nego Google.
    Što veći grad = duže traje.
    """)

# ---------- GLAVNI UNOS ----------
col1, col2 = st.columns(2)
with col1:
    preset_choice = st.selectbox("🏢 Tip firme", list(BUSINESS_PRESETS.keys()))
with col2:
    location = st.text_input("📍 Grad / Lokacija", placeholder="npr. Beograd")

custom_text = ""
if preset_choice == "Ostalo / Custom (slobodan tekst)":
    custom_text = st.text_input("✏️ Unesite ključnu reč (npr. pizza, salon, auto)", placeholder="npr. pizza")

# ---------- DUGME ----------
if st.button("🔍 Pretraži"):
    osm_filters = BUSINESS_PRESETS.get(preset_choice, [])

    if not location:
        st.warning("⚠️ Molimo unesite lokaciju.")
    elif preset_choice == "Ostalo / Custom (slobodan tekst)" and not custom_text:
        st.warning("⚠️ Molimo unesite ključnu reč za custom pretragu.")
    else:
        settings = {
            "location": location,
            "osm_filters": osm_filters,
            "custom_text": custom_text,
            "search_in_name": search_in_name,
            "timeout": timeout,
            "only_without_website": only_without_website,
            "only_with_phone": only_with_phone,
            "only_with_address": only_with_address,
        }

        with st.spinner("Pretražujem..."):
            progress = st.progress(0)
            status = st.empty()
            leads, display_name = find_leads(settings, progress, status)
            status.empty()

        if leads:
            # Ograniči na max_results
            leads = leads[:max_results]
            df = pd.DataFrame(leads)

            if display_name:
                st.caption(f"📍 Lokacija: {display_name}")

            # Statistika
            m1, m2, m3 = st.columns(3)
            m1.metric("Ukupno firmi", len(df))
            m2.metric("Sa telefonom", int((df["Telefon"] != "").sum()))
            m3.metric("Sa adresom", int((df["Adresa"] != "").sum()))

            st.success(f"✨ Pronađeno {len(df)} firmi!")
            st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False).encode("utf-8-sig")
            fname = f"klijenti_{preset_choice}_{location}.csv".replace(" ", "_")
            st.download_button("📥 Preuzmi CSV", data=csv, file_name=fname, mime="text/csv")
        else:
            st.info("Nije pronađena nijedna firma. Pokušajte drugi tip, grad ili olabavite filtere.")

st.markdown("---")
st.markdown('<p style="text-align:center; color:#6B7280;">Napravljeno sa ❤️</p>', unsafe_allow_html=True)
