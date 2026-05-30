from __future__ import annotations


# This file is NOT here to teach Gemini synonyms.
# It is here to help the backend search the LOCAL metadata catalogue.
#
# Why?
# A user may say: "temperature variation"
# But the local World Bank catalogue may not contain an exact "temperature" indicator.
# So we expand the request to searchable metadata terms:
# climate, environment, precipitation, drought, emissions, etc.
#
# The AI must then choose only from the local candidate indicators returned by the DB.

DOMAIN_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    # -------------------------------------------------------------------------
    # Climate / environment
    # -------------------------------------------------------------------------
    "temperature": (
        "température",
        "températures",
        "climat",
        "climatique",
        "changement climatique",
        "environnement",
        "précipitations",
        "sécheresse",
        "sécheresses",
        "inondations",
        "extrêmes",
        "pollution",
        "émissions",
        "co2",
        "gaz à effet de serre",
    ),
    "température": (
        "temperature",
        "températures",
        "climat",
        "climatique",
        "changement climatique",
        "environnement",
        "précipitations",
        "sécheresse",
        "sécheresses",
        "inondations",
        "extrêmes",
        "pollution",
        "émissions",
        "co2",
        "gaz à effet de serre",
    ),
    "climate": (
        "climat",
        "changement climatique",
        "environnement",
        "température",
        "précipitations",
        "sécheresse",
        "inondations",
        "émissions",
        "co2",
        "pollution",
        "gaz à effet de serre",
    ),
    "climat": (
        "climate",
        "changement climatique",
        "environnement",
        "température",
        "précipitations",
        "sécheresse",
        "inondations",
        "émissions",
        "co2",
        "pollution",
        "gaz à effet de serre",
    ),
    "environment": (
        "environnement",
        "climat",
        "changement climatique",
        "pollution",
        "émissions",
        "co2",
        "gaz à effet de serre",
        "eau",
        "forêt",
        "terres",
        "biodiversité",
        "aires protégées",
    ),
    "environnement": (
        "environment",
        "climat",
        "changement climatique",
        "pollution",
        "émissions",
        "co2",
        "gaz à effet de serre",
        "eau",
        "forêt",
        "terres",
        "biodiversité",
        "aires protégées",
    ),
    "co2": (
        "émissions",
        "dioxyde de carbone",
        "gaz à effet de serre",
        "environnement",
        "climat",
        "changement climatique",
        "pollution",
    ),
    "pollution": (
        "pollution",
        "pm2,5",
        "particules fines",
        "environnement",
        "air",
        "émissions",
    ),
    "precipitation": (
        "précipitations",
        "pluie",
        "climat",
        "sécheresse",
        "inondations",
        "environnement",
    ),
    "précipitation": (
        "precipitation",
        "précipitations",
        "pluie",
        "climat",
        "sécheresse",
        "inondations",
        "environnement",
    ),
    "precipitations": (
        "précipitations",
        "pluie",
        "climat",
        "sécheresse",
        "inondations",
        "environnement",
    ),
    "précipitations": (
        "precipitation",
        "rainfall",
        "pluie",
        "climat",
        "sécheresse",
        "inondations",
        "environnement",
    ),
    "drought": (
        "sécheresse",
        "sécheresses",
        "climat",
        "précipitations",
        "inondations",
        "température",
        "environnement",
    ),
    "sécheresse": (
        "drought",
        "sécheresses",
        "climat",
        "précipitations",
        "inondations",
        "température",
        "environnement",
    ),

    # -------------------------------------------------------------------------
    # Economy / GDP / growth
    # -------------------------------------------------------------------------
    "gdp": (
        "pib",
        "croissance",
        "croissance économique",
        "économie",
        "revenu",
        "revenu national",
        "rnb",
        "valeur ajoutée",
        "investissement",
        "inflation",
    ),
    "pib": (
        "gdp",
        "croissance",
        "croissance économique",
        "économie",
        "revenu",
        "revenu national",
        "rnb",
        "valeur ajoutée",
        "investissement",
        "inflation",
    ),
    "growth": (
        "croissance",
        "croissance économique",
        "pib",
        "économie",
        "investissement",
        "productivité",
        "revenu",
    ),
    "croissance": (
        "growth",
        "croissance économique",
        "pib",
        "économie",
        "investissement",
        "productivité",
        "revenu",
    ),
    "economy": (
        "économie",
        "croissance",
        "pib",
        "revenu",
        "rnb",
        "inflation",
        "investissement",
        "commerce",
    ),
    "économie": (
        "economy",
        "croissance",
        "pib",
        "revenu",
        "rnb",
        "inflation",
        "investissement",
        "commerce",
    ),
    "inflation": (
        "inflation",
        "prix",
        "indice des prix",
        "prix à la consommation",
        "déflateur",
        "pib",
        "économie",
    ),
    "investment": (
        "investissement",
        "investissements",
        "formation brute de capital",
        "capital",
        "ide",
        "investissements étrangers directs",
    ),
    "investissement": (
        "investment",
        "investissements",
        "formation brute de capital",
        "capital",
        "ide",
        "investissements étrangers directs",
    ),
    "trade": (
        "commerce",
        "commerce extérieur",
        "commerce exterieur",
        "exportations",
        "importations",
        "exports",
        "imports",
        "external trade",
        "ouverture commerciale",
        "dépendance commerciale",
        "dependance commerciale",
        "compte courant",
        "solde courant",
        "réserves internationales",
        "reserves internationales",
        "biens et services",
        "balance commerciale",
        "échanges commerciaux",
    ),
    "commerce": (
        "trade",
        "commerce extérieur",
        "commerce exterieur",
        "exportations",
        "importations",
        "exports",
        "imports",
        "external trade",
        "ouverture commerciale",
        "biens et services",
        "balance commerciale",
        "échanges commerciaux",
    ),
    "commerce extérieur": (
        "commerce exterieur",
        "exportations",
        "importations",
        "trade",
        "external trade",
        "exports",
        "imports",
        "ouverture commerciale",
        "balance commerciale",
    ),
    "commerce exterieur": (
        "commerce extérieur",
        "exportations",
        "importations",
        "trade",
        "external trade",
        "exports",
        "imports",
        "ouverture commerciale",
        "balance commerciale",
    ),
    "ouverture commerciale": (
        "commerce",
        "trade",
        "exports",
        "imports",
        "exportations",
        "importations",
        "commerce extérieur",
    ),
    "ide": (
        "investissement direct étranger",
        "investissement direct etranger",
        "foreign direct investment",
        "fdi",
        "investissements étrangers directs",
        "investissements etrangers directs",
    ),
    "fdi": (
        "investissement direct étranger",
        "investissement direct etranger",
        "ide",
        "foreign direct investment",
        "investissements étrangers directs",
    ),
    "investissement direct étranger": (
        "ide",
        "fdi",
        "foreign direct investment",
        "investissements étrangers",
        "investissements etrangers",
    ),
    "investissement direct etranger": (
        "ide",
        "fdi",
        "foreign direct investment",
        "investissements étrangers",
        "investissements etrangers",
    ),

    # -------------------------------------------------------------------------
    # Poverty / social / labor
    # -------------------------------------------------------------------------
    "poverty": (
        "pauvreté",
        "revenu",
        "inégalité",
        "emploi",
        "chômage",
        "social",
        "population",
        "conditions de vie",
    ),
    "pauvreté": (
        "poverty",
        "revenu",
        "inégalité",
        "emploi",
        "chômage",
        "social",
        "population",
        "conditions de vie",
    ),
    "unemployment": (
        "chômage",
        "emploi",
        "marché du travail",
        "population active",
        "travail",
        "social",
    ),
    "chômage": (
        "unemployment",
        "emploi",
        "marché du travail",
        "population active",
        "travail",
        "social",
    ),
    "employment": (
        "emploi",
        "chômage",
        "travail",
        "population active",
        "marché du travail",
    ),
    "emploi": (
        "employment",
        "chômage",
        "travail",
        "population active",
        "marché du travail",
    ),

    # -------------------------------------------------------------------------
    # Education
    # -------------------------------------------------------------------------
    "education": (
        "éducation",
        "scolarisation",
        "enseignement",
        "alphabétisation",
        "école",
        "primaire",
        "secondaire",
        "université",
        "inscription scolaire",
    ),
    "éducation": (
        "education",
        "scolarisation",
        "enseignement",
        "alphabétisation",
        "école",
        "primaire",
        "secondaire",
        "université",
        "inscription scolaire",
    ),
    "school": (
        "école",
        "scolarisation",
        "enseignement",
        "éducation",
        "primaire",
        "secondaire",
    ),
    "école": (
        "school",
        "scolarisation",
        "enseignement",
        "éducation",
        "primaire",
        "secondaire",
    ),

    # -------------------------------------------------------------------------
    # Health / population
    # -------------------------------------------------------------------------
    "health": (
        "santé",
        "mortalité",
        "espérance de vie",
        "dépenses de santé",
        "population",
        "naissance",
        "maladie",
        "médecins",
        "hôpital",
    ),
    "santé": (
        "health",
        "mortalité",
        "espérance de vie",
        "dépenses de santé",
        "population",
        "naissance",
        "maladie",
        "médecins",
        "hôpital",
    ),
    "population": (
        "population",
        "démographie",
        "naissance",
        "mortalité",
        "croissance démographique",
        "densité",
        "urbain",
        "rural",
    ),
    "démographie": (
        "population",
        "demography",
        "naissance",
        "mortalité",
        "croissance démographique",
        "densité",
        "urbain",
        "rural",
    ),

    # -------------------------------------------------------------------------
    # Energy / infrastructure
    # -------------------------------------------------------------------------
    "energy": (
        "énergie",
        "électricité",
        "combustibles",
        "renouvelable",
        "pétrole",
        "gaz",
        "consommation d’énergie",
        "accès à l’électricité",
    ),
    "énergie": (
        "energy",
        "électricité",
        "combustibles",
        "renouvelable",
        "pétrole",
        "gaz",
        "consommation d’énergie",
        "accès à l’électricité",
    ),
    "electricity": (
        "électricité",
        "énergie",
        "accès à l’électricité",
        "production d’électricité",
        "consommation d’électricité",
    ),
    "électricité": (
        "electricity",
        "énergie",
        "accès à l’électricité",
        "production d’électricité",
        "consommation d’électricité",
    ),

    # -------------------------------------------------------------------------
    # Debt / finance
    # -------------------------------------------------------------------------
    "debt": (
        "dette",
        "dette extérieure",
        "service de la dette",
        "fmi",
        "rnb",
        "exportations",
        "financement",
    ),
    "dette": (
        "debt",
        "dette extérieure",
        "service de la dette",
        "fmi",
        "rnb",
        "exportations",
        "financement",
    ),
    "finance": (
        "finance",
        "secteur financier",
        "banques",
        "crédit",
        "réserves",
        "taux d’intérêt",
        "inflation",
    ),
    "financier": (
        "finance",
        "secteur financier",
        "banques",
        "crédit",
        "réserves",
        "taux d’intérêt",
        "inflation",
    ),
}


COUNTRY_ALIASES: dict[str, str] = {
    "chine": "Chine",
    "china": "Chine",
    "chn": "Chine",
    "cn": "Chine",
    "republique populaire de chine": "Chine",
    "république populaire de chine": "Chine",

    "mauritania": "Mauritanie",
    "mauritanie": "Mauritanie",
    "mrt": "Mauritanie",
    "mr": "Mauritanie",

    "usa": "États-Unis",
    "united states": "États-Unis",
    "united states of america": "États-Unis",
    "etats-unis": "États-Unis",
    "états-unis": "États-Unis",

    "south africa": "Afrique du Sud",
    "afrique du sud": "Afrique du Sud",

    "morocco": "Maroc",
    "maroc": "Maroc",
    "mar": "Maroc",
    "ma": "Maroc",

    "senegal": "Sénégal",
    "sénégal": "Sénégal",
    "sen": "Sénégal",
    "sn": "Sénégal",

    "france": "France",
    "fra": "France",
    "fr": "France",

    "mali": "Mali",
    "niger": "Niger",
    "tunisia": "Tunisie",
    "tunisie": "Tunisie",
    "algeria": "Algérie",
    "algérie": "Algérie",
    "algerie": "Algérie",
}


DEFAULT_SEARCH_TERMS: tuple[str, ...] = (
    "économie",
    "croissance",
    "pib",
    "population",
)


def normalize_country_query(country_name: str | None) -> str:
    """
    Converts common English/coded country names to the French country names
    stored in the local catalogue when possible.
    """

    clean = (country_name or "").strip().lower()

    if not clean:
        return "Mauritanie"

    return COUNTRY_ALIASES.get(clean, country_name or "Mauritanie")


def expand_domain_terms(keywords: list[str]) -> list[str]:
    """
    Expands keywords using controlled vocabulary.

    This is backend search support, not AI reasoning.
    """

    expanded: list[str] = []

    for keyword in keywords:
        clean = keyword.strip().lower()

        if not clean:
            continue

        expanded.append(clean)

        for key, related_terms in DOMAIN_SEARCH_TERMS.items():
            if clean == key or key in clean or clean in key:
                expanded.extend(related_terms)

    if not expanded:
        expanded.extend(DEFAULT_SEARCH_TERMS)

    final_terms: list[str] = []
    seen: set[str] = set()

    for term in expanded:
        clean = term.strip().lower()

        if not clean or len(clean) < 2:
            continue

        if clean in seen:
            continue

        seen.add(clean)
        final_terms.append(clean)

    return final_terms
