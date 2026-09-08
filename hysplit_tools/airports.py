"""
Airports appearing in this project's METAR crosscheck data (the 11 ICAO
codes cross-referenced in Dust_ArabianPeninsula_*_metar_dust_crosscheck.csv),
with real published airport reference-point coordinates rather than the
nearest-MERRA-grid-point values used for the crosscheck itself.
"""

AIRPORTS = {
    "OBBI": {"name": "Bahrain International", "lat": 26.2708, "lon": 50.6336},
    "OEDF": {"name": "King Fahd International", "lat": 26.4712, "lon": 49.7979},
    "OEDR": {"name": "King Abdulaziz Air Base (Dhahran)", "lat": 26.2712, "lon": 50.1520},
    "OEJN": {"name": "King Abdulaziz International (Jeddah)", "lat": 21.6796, "lon": 39.1565},
    "OEMA": {"name": "Prince Mohammad Bin Abdulaziz (Madinah)", "lat": 24.5534, "lon": 39.7051},
    "OERK": {"name": "King Khalid International (Riyadh)", "lat": 24.9576, "lon": 46.6988},
    "OMAA": {"name": "Zayed International (Abu Dhabi)", "lat": 24.4330, "lon": 54.6511},
    "OMDB": {"name": "Dubai International", "lat": 25.2532, "lon": 55.3657},
    "OMDW": {"name": "Al Maktoum International (Dubai World Central)", "lat": 24.8964, "lon": 55.1614},
    "OMSJ": {"name": "Sharjah International", "lat": 25.3286, "lon": 55.5172},
    "OTHH": {"name": "Hamad International (Doha)", "lat": 25.2731, "lon": 51.6086},
}


def plot_airports(ax, icao_codes, lon_min=None, lon_max=None, lat_min=None, lat_max=None):
    """Draws triangle markers + ICAO code labels for the given airports on an existing axis."""
    for code in icao_codes:
        info = AIRPORTS.get(code)
        if not info:
            continue
        lat, lon = info["lat"], info["lon"]
        if lon_min is not None and not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            continue
        ax.scatter(lon, lat, marker="^", s=70, c="lime", edgecolors="black", linewidths=0.7, zorder=5)
        ax.annotate(code, (lon, lat), textcoords="offset points", xytext=(5, 4),
                    fontsize=8, color="white", zorder=5)
