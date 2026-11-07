"""
places.py - the cities Yo Courier serves, with their position on the map (latitude, longitude).

Yo Delivery started in the Ambala Division of Haryana (the university project), so the home area
has the most towns. Distances between cities are measured "as the crow flies" (haversine formula)
and multiplied by ROAD_FACTOR, because roads are never straight.

Inside one city the shop has no map service (no paid geocoding), so the two addresses get an
approximate position near the city centre - or the exact one, when the sender taps "Use my location".
"""

import hashlib
import math

ROAD_FACTOR = 1.3
HOME_CITY = "Ambala Cantt"          # the main Yo hub

# name: (state, PIN code, latitude, longitude, region)
#   region "home" = Ambala Division and the Tricity (regional rates, next-day delivery)
CITIES = {
    "Ambala Cantt": ("Haryana", "133001", 30.3360, 76.8480, "home"),
    "Ambala City": ("Haryana", "134003", 30.3782, 76.7767, "home"),
    "Mullana": ("Haryana", "133207", 30.2830, 77.0490, "home"),
    "Barara": ("Haryana", "133201", 30.2097, 77.0383, "home"),
    "Naraingarh": ("Haryana", "134203", 30.4772, 77.1283, "home"),
    "Kurukshetra": ("Haryana", "136118", 29.9695, 76.8783, "home"),
    "Shahabad Markanda": ("Haryana", "136135", 30.1680, 76.8700, "home"),
    "Pehowa": ("Haryana", "136128", 29.9800, 76.5800, "home"),
    "Kaithal": ("Haryana", "136027", 29.8015, 76.3998, "home"),
    "Yamunanagar": ("Haryana", "135001", 30.1290, 77.2674, "home"),
    "Jagadhri": ("Haryana", "135003", 30.1686, 77.3010, "home"),
    "Panchkula": ("Haryana", "134109", 30.6942, 76.8606, "home"),
    "Pinjore": ("Haryana", "134102", 30.7983, 76.9180, "home"),
    "Kalka": ("Haryana", "133302", 30.8398, 76.9396, "home"),
    "Chandigarh": ("Chandigarh", "160017", 30.7333, 76.7794, "home"),
    "Mohali": ("Punjab", "160062", 30.7046, 76.7179, "home"),
    "Zirakpur": ("Punjab", "140603", 30.6425, 76.8173, "home"),
    "Karnal": ("Haryana", "132001", 29.6857, 76.9905, "home"),
    "Patiala": ("Punjab", "147001", 30.3398, 76.3869, "home"),
    "Ludhiana": ("Punjab", "141001", 30.9010, 75.8573, "india"),
    "Dehradun": ("Uttarakhand", "248001", 30.3165, 78.0322, "india"),
    "New Delhi": ("Delhi", "110001", 28.6139, 77.2090, "india"),
    "Gurugram": ("Haryana", "122001", 28.4595, 77.0266, "india"),
    "Noida": ("Uttar Pradesh", "201301", 28.5355, 77.3910, "india"),
    "Jaipur": ("Rajasthan", "302001", 26.9124, 75.7873, "india"),
    "Lucknow": ("Uttar Pradesh", "226001", 26.8467, 80.9462, "india"),
    "Ahmedabad": ("Gujarat", "380001", 23.0225, 72.5714, "india"),
    "Mumbai": ("Maharashtra", "400001", 19.0760, 72.8777, "india"),
    "Pune": ("Maharashtra", "411001", 18.5204, 73.8567, "india"),
    "Hyderabad": ("Telangana", "500001", 17.3850, 78.4867, "india"),
    "Bengaluru": ("Karnataka", "560001", 12.9716, 77.5946, "india"),
    "Chennai": ("Tamil Nadu", "600001", 13.0827, 80.2707, "india"),
    "Kolkata": ("West Bengal", "700001", 22.5726, 88.3639, "india"),
}

# a few big cities abroad - only to draw the map of an international parcel
WORLD_CITIES = {
    "AE": ("Dubai", 25.2048, 55.2708), "RU": ("Moscow", 55.7558, 37.6173), "RW": ("Kigali", -1.9441, 30.0619),
    "FR": ("Paris", 48.8566, 2.3522), "ES": ("Madrid", 40.4168, -3.7038), "CN": ("Beijing", 39.9042, 116.4074),
    "GB": ("London", 51.5072, -0.1276), "US": ("New York", 40.7128, -74.0060), "NP": ("Kathmandu", 27.7172, 85.3240),
    "SG": ("Singapore", 1.3521, 103.8198), "DE": ("Berlin", 52.5200, 13.4050), "KE": ("Nairobi", -1.2921, 36.8219),
}


def city(name):
    """Find a city by its name (any letter case, extra spaces allowed). Returns the name as written here."""
    key = " ".join(str(name or "").split()).lower()
    for n in CITIES:
        if n.lower() == key:
            return n
    return None


def position(name):
    c = CITIES[name]
    return c[2], c[3]


def haversine_km(a, b):
    """Distance in km between two (lat, lng) points on the Earth (great-circle distance)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def road_km(a, b):
    return round(haversine_km(a, b) * ROAD_FACTOR, 1)


def near_centre(name, text):
    """An approximate point 1-4 km from the city centre, always the same for the same address text."""
    lat, lng = position(name)
    h = hashlib.sha256((name + "|" + " ".join(str(text).lower().split())).encode()).digest()
    angle = h[0] / 255 * 2 * math.pi
    dist_km = 1 + h[1] / 255 * 3
    return (round(lat + dist_km / 111.0 * math.cos(angle), 5),
            round(lng + dist_km / (111.0 * math.cos(math.radians(lat))) * math.sin(angle), 5))


def point_ok(name, lat, lng, limit_km=40):
    """A point sent by the browser ("use my location") is accepted only near the chosen city."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    return haversine_km(position(name), (lat, lng)) <= limit_km


def along(points, fraction):
    """The point at `fraction` (0..1) of the way along a line through `points` (used by the demo couriers)."""
    fraction = max(0.0, min(1.0, fraction))
    legs = [haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(legs)
    if total == 0:
        return points[-1]
    goal, done = total * fraction, 0.0
    for i, leg in enumerate(legs):
        if done + leg >= goal and leg > 0:
            t = (goal - done) / leg
            a, b = points[i], points[i + 1]
            return (round(a[0] + (b[0] - a[0]) * t, 5), round(a[1] + (b[1] - a[1]) * t, 5))
        done += leg
    return points[-1]


def cities_near(points, limit=14):
    """Known towns inside the box around some points - the map shows them as small dots with names."""
    lats, lngs = [p[0] for p in points], [p[1] for p in points]
    pad = max(0.08, (max(lats) - min(lats)) * 0.25, (max(lngs) - min(lngs)) * 0.25)
    box = (min(lats) - pad, max(lats) + pad, min(lngs) - pad, max(lngs) + pad)
    found = [(n, c[2], c[3]) for n, c in CITIES.items() if box[0] <= c[2] <= box[1] and box[2] <= c[3] <= box[3]]
    found += [(c[0], c[1], c[2]) for c in WORLD_CITIES.values()
              if box[0] <= c[1] <= box[1] and box[2] <= c[2] <= box[3]]
    return found[:limit]
