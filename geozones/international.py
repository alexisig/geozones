from . import wiki
from .model import country, country_group
from .tools import info, warning, success, progress

_ = lambda s: s  # noqa: E731


# This is the latest URL (currently 4.1.0)
# There is no versionned adress right now
NE_URL = ('https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/'
          '110m/cultural/ne_110m_admin_0_countries_lakes.zip')

# Natural Earth stores None as '-99'
NE_NONE = '-99'

# Associate some fixes to Natural Earth polygons.
# Use the `NE_ID` attribute to identify each polygon
NE_FIXES = {
    # France is lacking ISO codes
    1159320637: {
        'ISO_A2': 'FR',
        'ISO_A3': 'FRA',
    },
    # Norway is lacking ISO codes
    1159321109: {
        'ISO_A2': 'NO',
        'ISO_A3': 'NOR',
    }
}


def ne_prop(props, key, cast=str):
    '''
    Fetch a Natural Eartch property.

    This method handle:
    - both upper and lower case as casing has been changing between releases
    - try with a trailing underscore (some properties are moving)
    - returns `None` instead of `-99`
    - match fixes if any
    - perform a cast if necessary
    - case lowering
    '''
    ne_id = props['NE_ID']
    upper_key = key.upper()
    keys = (upper_key, key.lower(), upper_key + '_', key.lower() + '_')
    value = None
    for key in keys:
        if key in props:
            value = props[key]
            continue
    if value == NE_NONE:  # None value for Natural Earth
        if ne_id in NE_FIXES and upper_key in NE_FIXES[ne_id]:
            value = NE_FIXES[ne_id][upper_key]
        else:
            return None
    if not value:
        return None
    elif cast is str:
        return value.lower()
    else:
        return cast(value)


@country.extractor(NE_URL, encoding='utf-8')
def extract_country(db, polygon):
    '''
    Extract a country information from single MultiPolygon.
    Based on data from:
    http://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-admin-0-countries/

    The main unique code used is ISO2.
    '''
    props = polygon['properties']
    code = ne_prop(props, 'ISO_A2')
    if not code:
        warning('Missing iso code 2 for {NAME}, skipping'.format(**props))
        return
    return {
        'code': code,
        'name': props['NAME'],
        'population': ne_prop(props, 'POP_EST', int),
        'parents': ['country-group:world'],
        'keys': {
            'iso2': code,
            'iso3': ne_prop(props, 'ISO_A3'),
            'un': ne_prop(props, 'UN_A3'),
            'fips': ne_prop(props, 'FIPS_10'),
        }
    }


@country.extractor('https://github.com/apihackers/geo-countries-simplified/releases/download/'
                   '2019-05-06/countries.geojson')
def extract_countries(db, polygon):
    '''
    Use cleaner shapes from Datahub geo countries: https://datahub.io/core/geo-countries
    '''
    props = polygon['properties']
    return next(db.level(country.id, **{'keys.iso3': props['ISO_A3'].lower()}), None)


# World Aggregate
country_group.aggregate(
    'world', _('World'),
    ['country:*'],
    keys={'default': 'world'},
    wikidata='Q2',
)


# European union
UE_COUNTRIES = (
    'at', 'be', 'bg', 'cy', 'hr', 'dk', 'ee', 'fi', 'gr', 'fr', 'es', 'de',
    'hu', 'ie', 'it', 'lv', 'lt', 'lu', 'mt', 'nl', 'no', 'pl', 'pt', 'cz',
    'ro', 'gb', 'sk', 'si', 'se'
)

country_group.aggregate(
    'ue', _('European Union'),
    ['country:{0}'.format(code) for code in UE_COUNTRIES],
    parents=['country-group:world'],
    keys={'default': 'ue'},
    wikipedia='en:European_Union',
    wikidata='Q458',
)


@country.postprocessor()
def add_ue_to_parents(db):
    info('Adding European Union to countries parents')
    result = db.update_many(
        {'level': country.id, 'code': {'$in': UE_COUNTRIES}},
        {'$addToSet': {'parents': 'country-group:ue'}})
    success('Added European Union as parent to {0} countries',
            result.modified_count)


COUNTRY_GROUPS_SPARQL_QUERY = '''
SELECT ?grp ?grpLabel ?population ?area ?geonames ?osm ?flag ?site ?wikipedia
WHERE
{{
  VALUES ?grp {{ {ids} }}
  ?grp wdt:P2046 ?area;
       wdt:P1082 ?population.
  OPTIONAL {{?grp wdt:P1566 ?geonames.}}
  OPTIONAL {{?grp wdt:P41 ?flag.}}
  OPTIONAL {{?grp wdt:P402 ?osm.}}
  OPTIONAL {{?grp wdt:P856 ?site.}}
  OPTIONAL {{?wikipedia schema:about ?grp;
                       schema:inLanguage 'en';
                       schema:isPartOf <https://en.wikipedia.org/>.
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,fr". }}
}}
'''


@country_group.postprocessor()
def fetch_country_groups_data_from_wikidata(db):
    info('Fetching country-groups wikidata metadata')
    groups = list(db.level(country_group.id, db.TODAY, wikidata={'$exists': True}))
    ids = {grp['wikidata']: grp['_id'] for grp in groups}

    wdids = ' '.join(f'wd:{id}' for id in ids.keys())
    query = COUNTRY_GROUPS_SPARQL_QUERY.format(ids=wdids)
    results = wiki.data_sparql_query(query)
    results = wiki.data_reduce_result(results, 'grp')

    for row in results:
        uri = row['grp']
        wdid = wiki.data_uri_to_id(uri)
        zone_id = ids.get(wdid)
        if zone_id:
            db.find_one_and_update({'_id': zone_id}, {
                '$set': {k: v for k, v in {
                    'wikidata': wdid,
                    'wikipedia': wiki.wikipedia_url_to_id(row['wikipedia']),
                    'dbpedia': wiki.wikipedia_to_dbpedia(row['wikipedia']),
                    'website': row.get('site'),
                    'flag': wiki.media_url_to_path(row.get('flag')),
                    'area': float(row.get('area', 0)) or None,
                    'population': int(row.get('population', 0)) or None,
                    'keys.osm': row.get('osm'),
                    'keys.geonames': row.get('geonames'),
                }.items() if v is not None}
            })


COUNTRIES_SPARQL_QUERY = '''
SELECT DISTINCT ?country ?countryLabel ?population ?area ?iso2 ?iso3 ?geonames ?osm ?nuts ?flag ?site ?wikipedia
WHERE
{
  ?country wdt:P31 wd:Q3624078;
           wdt:P36 ?capital;
           wdt:P2046 ?area;
           wdt:P1082 ?population;
           p:P297 ?iso2Stmt.
  ?iso2Stmt ps:P297 ?iso2.
  FILTER NOT EXISTS { ?iso2Stmt pq:P582 [] } .
  OPTIONAL {?country p:P298 ?iso3Stmt.
            ?iso3Stmt ps:P298 ?iso3.
            FILTER NOT EXISTS { ?iso3Stmt pq:P582 [] } .
            }
  OPTIONAL {?country p:P605 ?nutsStmt.
            ?nutsStmt ps:P605 ?nuts.
            FILTER (regex(?nuts, '^\\\\w{2}$')) .
            FILTER NOT EXISTS { ?nutsStmt pq:P582 [] } .
            }
  OPTIONAL {?country wdt:P901 ?fips.}
  OPTIONAL {?country wdt:P1566 ?geonames.}
  OPTIONAL {?country wdt:P41 ?flag.}
  OPTIONAL {?country wdt:P402 ?osm.}
  OPTIONAL {?country wdt:P856 ?site.}
  OPTIONAL {?wikipedia schema:about ?country;
                       schema:inLanguage 'en';
                       schema:isPartOf <https://en.wikipedia.org/>.
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "   [AUTO_LANGUAGE],en,fr". }
}
'''


@country.postprocessor()
def fetch_country_data_from_wikidata(db):
    info('Fetching countries wikidata metadata')
    results = wiki.data_sparql_query(COUNTRIES_SPARQL_QUERY)
    results = wiki.data_reduce_result(results, 'country')
    for row in progress(results):
        iso2 = row['iso2'].lower()
        db.update_zone(country.id, iso2, ops={
            '$set': {k: v for k, v in {
                'wikidata': wiki.data_uri_to_id(row['country']),
                'wikipedia': wiki.wikipedia_url_to_id(row['wikipedia']),
                'dbpedia': wiki.wikipedia_to_dbpedia(row['wikipedia']),
                'website': row.get('site'),
                'flag': wiki.media_url_to_path(row['flag']),
                'area': float(row['area']),
                'population': int(row['population']),
                'keys.iso3': row.get('iso3', '').lower() or None,
                'keys.nuts': row.get('nuts', '').lower() or None,
                'keys.osm': row.get('osm'),
                'keys.fips': row.get('fips', '').lower() or None,
                'keys.geonames': row.get('geonames'),
            }.items() if v is not None}
        })
