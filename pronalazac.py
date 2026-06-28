import streamlit as st
import requests
import pandas as pd

# ============================================
# KONFIGURACIJA
# ============================================
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {"User-Agent": "PronalazacKlijenata/1.0"}


# ============================================
# FUNKCIJE
# ============================================
def get_location_bbox(location):
    """Pronađi geografske granice grada preko Nominatim."""
    params = {"q": location, "format": "json", "limit": 1}
    resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS).json()
    if not resp:
        return None
    bbox = resp[0]["boundingbox"]  # [south, north, west, east]
    return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])


def build_query(business_type, bbox):
    south, north, west, east = bbox
    # Tražimo po imenu (name) i tipu (shop/amenity)
    return f"""
    [out:json][timeout:30];
    (
      node["name"~"{business_type}",i]({south},{west},{north},{east});
      way["name"~"{business_type}",i]({south},{west},{north},{east});
      node["shop"~"{business_type}",i]({south},{west},{north},{east});
      node["amenity"~"{business_type}",i]({south},{west},{north},{east});
    );
    out center;
    """


def find_leads(business_type, location, progress_bar, status_text):
    status_text.text("Tražim lokaciju...")
    bbox = get_location_bbox(location)
    if not bbox:
        st.error("Nije pronađena lokacija. Pokušajte drugačije ime grada.")
        return []

    status_text.text("Pretražujem firme...")
    query = build_query(business_type, bbox)

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=60)
        data = resp.json()
    except Exception as e:
        st.error(f"Greška pri konekciji: {e}")
        return []

    elements = data.get("elements", [])
    total = len(elements)
    leads = []
    seen_names = set()

    for i, el in enumerate(elements, 1):
        tags = el.get("tags", {})
        name = tags.get("name", "").strip()

        if not name or name in seen_names:
            continue

        # FILTER: preskoči ako IMA veb sajt
        website = tags.get("website") or tags.get("contact:website") or tags.get("url")
        if website:
            progress_bar.progress(i / total if total else 1.0)
            status_text.text(f"Preskačem (ima sajt): {name}")
            continue

        seen_names.add(name)

        # Sastavi adresu iz delova
        street = tags.get("addr:street", "")
        housenumber = tags.get("addr:housenumber", "")
        city = tags.get("addr:city", "")
        postcode = tags.get("addr:postcode", "")
        address = " ".join(filter(None, [street, housenumber, postcode, city]))

        phone = tags.get("phone") or tags.get("contact:phone", "")

        # Koordinate (za Google Maps link)
        lat = el.get("lat") or el.get("center", {}).get("lat", "")
        lon = el.get("lon") or el.get("center", {}).get("lon", "")
        maps_link = f"https://www.google.com/maps?q={lat},{lon}" if lat else ""

        lead = {
            "Naziv": name,
            "Telefon": phone,
            "Adresa": address,
            "Tip": tags.get("shop") or tags.get("amenity", ""),
            "Mapa link": maps_link,
        }
        leads.append(lead)
        progress_bar.progress(i / total if total else 1.0)
        status_text.text(f"✅ Pronađeno: {name}")

    return leads


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
    .stTextInput>div>div>input {
        background-color: #1F2937; color: white;
        border-radius: 10px; border: 1px solid #374151; padding: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-title">🎯 Pronalazač Klijenata</p>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Pronađite firme BEZ veb sajta (OpenStreetMap - besplatno)</p>', unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Podešavanja")
    st.success("✅ Ne treba API ključ! Koristi besplatni OpenStreetMap.")
    st.markdown("---")
    st.markdown("### 📖 Uputstvo")
    st.markdown("""
    1. Unesite **tip firme** (npr. frizer, restoran)
    2. Unesite **lokaciju** (grad)
    3. Kliknite **Pretraži**

    *Prikazuju se SAMO firme bez sajta.*

    ⚠️ Podaci su manje potpuni nego Google.
    """)

col1, col2 = st.columns(2)
with col1:
    business_type = st.text_input("🏢 Tip firme", placeholder="npr. frizer")
with col2:
    location = st.text_input("📍 Grad / Lokacija", placeholder="npr. Beograd")

if st.button("🔍 Pretraži"):
    if not business_type or not location:
        st.warning("⚠️ Molimo popunite tip firme i lokaciju.")
    else:
        with st.spinner("Pretražujem..."):
            progress = st.progress(0)
            status = st.empty()
            leads = find_leads(business_type, location, progress, status)
            status.empty()

        if leads:
            st.success(f"✨ Pronađeno {len(leads)} firmi BEZ sajta!")
            df = pd.DataFrame(leads)
            st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False).encode("utf-8-sig")
            fname = f"klijenti_{business_type}_{location}.csv"
            st.download_button("📥 Preuzmi CSV", data=csv, file_name=fname, mime="text/csv")
        else:
            st.info("Nije pronađena nijedna firma bez sajta. Pokušajte drugi tip ili grad.")

st.markdown("---")
st.markdown('<p style="text-align:center; color:#6B7280;">Napravljeno sa ❤️</p>', unsafe_allow_html=True)