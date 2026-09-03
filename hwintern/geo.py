"""Best-effort country resolution for the messy location strings job boards emit.

countries_for("Austin, TX")            -> {"US"}
countries_for("Munich, Germany; SF")   -> {"DE", "US"}
countries_for("Bengaluru, India")      -> {"OTHER"}      (recognised, but not one we track)
countries_for("Remote")                -> set()          (unknown)
"""
from __future__ import annotations

import re

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}
US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
]
CA_PROVINCES = {"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "PEI", "YT", "NT", "NU"}

# country -> (names, unambiguous cities)
COUNTRIES: dict[str, tuple[list[str], list[str]]] = {
    "US": (["united states", "usa", "u.s.a", "u.s.", "us", "america"],
           ["sf", "san francisco", "bay area", "silicon valley", "los angeles", "la", "nyc", "new york city",
            "seattle", "boston", "austin", "san jose", "palo alto", "mountain view", "sunnyvale", "santa clara",
            "cupertino", "menlo park", "redwood city", "fremont", "san diego", "irvine", "el segundo", "hawthorne",
            "torrance", "long beach", "pasadena", "denver", "boulder", "chicago", "detroit", "pittsburgh",
            "philadelphia", "atlanta", "dallas", "houston", "phoenix", "tempe", "chandler", "portland", "raleigh",
            "durham", "cambridge, ma", "huntsville", "orlando", "melbourne, fl", "minneapolis", "st. louis",
            "kent, wa", "redmond", "bellevue", "hillsboro", "folsom", "santa barbara", "san mateo", "hayward",
            "south sf", "south san francisco", "remote - us", "remote, us", "us remote", "remote (us)", "us-remote"]),
    "CA": (["canada"],
           ["toronto", "vancouver", "montreal", "montréal", "ottawa", "calgary", "waterloo", "kitchener", "edmonton",
            "mississauga", "markham", "quebec city", "burnaby", "victoria, bc", "winnipeg", "halifax"]),
    "GB": (["united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales", "northern ireland"],
           ["london", "bristol", "manchester", "birmingham, uk", "edinburgh", "glasgow", "oxford", "cambridge, uk",
            "reading", "leeds", "sheffield", "southampton", "belfast", "stevenage", "farnborough", "milton keynes"]),
    "DE": (["germany", "deutschland"],
           ["munich", "münchen", "berlin", "hamburg", "stuttgart", "frankfurt", "cologne", "köln", "düsseldorf",
            "dusseldorf", "dresden", "nuremberg", "nürnberg", "erlangen", "karlsruhe", "heidelberg", "aachen",
            "bremen", "hanover", "hannover", "leipzig", "ingolstadt", "wolfsburg", "böblingen", "ulm", "regensburg"]),
    "FR": (["france"],
           ["paris", "lyon", "toulouse", "grenoble", "nice", "marseille", "bordeaux", "nantes", "sophia antipolis",
            "sophia-antipolis", "rennes", "lille", "strasbourg", "montpellier", "aix-en-provence", "valbonne",
            "vélizy", "velizy", "saclay", "palaiseau", "issy-les-moulineaux", "boulogne-billancourt"]),
    "IT": (["italy", "italia"],
           ["milan", "milano", "rome", "roma", "turin", "torino", "bologna", "genoa", "genova", "naples", "napoli",
            "pisa", "padua", "padova", "florence", "firenze", "trieste", "catania", "modena", "bergamo", "brescia",
            "agrate brianza", "ivrea", "trento"]),
    "CH": (["switzerland", "schweiz", "suisse", "svizzera"],
           ["zurich", "zürich", "geneva", "genève", "geneve", "lausanne", "basel", "bern", "berne", "lugano",
            "winterthur", "zug", "neuchâtel", "neuchatel", "baden, switzerland", "lucerne", "luzern"]),
    "ES": (["spain", "españa", "espana"],
           ["madrid", "barcelona", "valencia", "seville", "sevilla", "bilbao", "malaga", "málaga", "zaragoza",
            "alicante", "murcia", "vigo", "girona", "san sebastián", "donostia"]),
}

OTHER_COUNTRIES = [
    "india", "china", "egypt", "israel", "japan", "korea", "singapore", "taiwan", "mexico", "brazil", "australia",
    "netherlands", "ireland", "poland", "sweden", "denmark", "norway", "finland", "belgium", "austria", "portugal",
    "czech", "czechia", "hungary", "romania", "greece", "turkey", "türkiye", "united arab emirates", "uae", "dubai",
    "saudi", "qatar", "morocco", "tunisia", "algeria", "nigeria", "kenya", "south africa", "vietnam", "thailand",
    "malaysia", "indonesia", "philippines", "pakistan", "bangladesh", "sri lanka", "argentina", "chile", "colombia",
    "peru", "new zealand", "hong kong", "russia", "ukraine", "serbia", "croatia", "slovakia", "slovenia", "bulgaria",
    "lithuania", "latvia", "estonia", "luxembourg", "iceland", "cyprus", "malta", "costa rica", "puerto rico",
    "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "mumbai", "delhi", "noida", "gurgaon", "gurugram",
    "shanghai", "beijing", "shenzhen", "suzhou", "hangzhou", "tokyo", "osaka", "seoul", "taipei", "hsinchu",
    "tel aviv", "haifa", "cairo", "amsterdam", "eindhoven", "dublin", "cork", "warsaw", "krakow", "kraków",
    "stockholm", "gothenburg", "lund", "copenhagen", "oslo", "helsinki", "espoo", "brussels", "leuven", "vienna",
    "graz", "lisbon", "porto", "prague", "brno", "budapest", "bucharest", "athens", "istanbul", "ankara", "sydney",
    "melbourne, australia", "melbourne, vic", "brisbane", "perth", "auckland", "são paulo", "sao paulo",
    "mexico city", "guadalajara", "monterrey", "penang", "kuala lumpur", "ho chi minh", "hanoi", "manila", "bangkok",
]

_SPLIT_RE = re.compile(r"\s*(?:;|\||/| or |\band\b|·|•|\+)\s*", re.I)


def _norm(s: str) -> str:
    s = s.lower().replace(" ", " ")
    s = re.sub(r"\(([^)]*)\)", r" \1 ", s)   # "(Remote)" -> " remote "
    s = re.sub(r"[\s,]+", " ", s).strip()
    return s


def _country_of_segment(seg: str) -> set[str]:
    raw = seg.strip()
    n = _norm(raw)
    found: set[str] = set()
    if not n:
        return found
    # 1. explicit country names / aliases (word-bounded)
    for code, (names, _) in COUNTRIES.items():
        for name in names:
            if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", n):
                found.add(code)
    for name in OTHER_COUNTRIES:
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", n):
            found.add("OTHER")
    if found:
        return found
    # 2. "City, ST" style US states and Canadian provinces (case-sensitive on the original)
    tokens = [t.strip(" .()") for t in re.split(r"[,\s]+", raw) if t.strip(" .()")]
    for t in tokens[1:] if len(tokens) > 1 else []:
        if t in US_STATES:
            found.add("US")
        elif t in CA_PROVINCES:
            found.add("CA")
    if found:
        return found
    # 3. full US state names
    for st in US_STATE_NAMES:
        if re.search(rf"(?<![a-z]){st}(?![a-z])", n):
            return {"US"}
    # 4. unambiguous cities
    for code, (_, cities) in COUNTRIES.items():
        for city in cities:
            if n == city or re.search(rf"(?<![a-z]){re.escape(city)}(?![a-z])", n):
                found.add(code)
    return found


def countries_for(location: str) -> set[str]:
    """Union of countries mentioned across all segments of a location string."""
    if not location:
        return set()
    out: set[str] = set()
    for seg in _SPLIT_RE.split(location):
        out |= _country_of_segment(seg)
    return out


def is_remote(location: str) -> bool:
    return bool(re.search(r"\bremote\b|\bwork from home\b|\bwfh\b", location or "", re.I))
