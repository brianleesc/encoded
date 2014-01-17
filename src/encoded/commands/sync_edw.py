"""\
Example.

To load the initial data:

    %(prog)s production.ini

"""
import sys
import logging
import datetime
import argparse
import copy
import re
from csv import DictReader
from urlparse import urlparse
from operator import itemgetter

from pyramid.paster import get_app
from webtest import TestApp, AppError

from encoded import edw_file

################
# Globals

# Change this when file schema changes
FILE_SCHEMA_VERSION = '1'

DEFAULT_INI = 'production.ini'  # Default application initialization file

ENCODE2_ACC = 'wgEncodeE'  # WARNING: Also in experiment.json and edw_file.py
ENCODE3_ACC = 'ENC'
ENCODE3_EXP_ACC = ENCODE3_ACC + 'SR'  # WARNING: Also in experiment.json
ENCODE2_EXP_PROP = 'encode2_dbxrefs' # WARNING: Also in experiment.json
ENCODE2_DS_PROP = 'aliases' ## ucsc_encode_db:wgEncodeXXX
# Schema object names
FILES = 'files'
EXPERIMENTS = 'experiments'
REPLICATES = 'replicates'
DATASETS = 'datasets'
USERS = 'users'

FILE_PROFILE_URL = '/profiles/file.json'

app_host_name = 'localhost'

EPILOG = __doc__

NO_UPDATE = ['md5sum', 'replicate', 'dataset']
IMPORT_USER = 'IMPORT'

logger = logging.getLogger(__name__)

encode2_to_encode3 = {}  # Dict of ENCODE3 accs, keyed by ENCODE 2 acc
encode3_to_encode2 = {}    # Cache experiment ENCODE 2 ref lists

experiments = {}
datasets = {}

def convert_edw(app, file_dict, phase=edw_file.ENCODE_PHASE_ALL):
    ''' converts EDW file structure to encoded object'''

    logger.info('Found EDW file: %s' % (file_dict['accession']))

    # convert time stamp
    valid_time = file_dict['date_created']
    if (type(valid_time) == float or type(valid_time) == long):
        file_dict['date_created'] = datetime.datetime.fromtimestamp(
           valid_time).strftime('%Y-%m-%d')
    elif not ( ( type(valid_time) == str or type(valid_time) == unicode ) and re.match('\d+-\d+\d+', valid_time) ):
        logger.error("Invalid time string: %s" % valid_time)
        sys.exit(1)


    if (file_dict['lab_error_message'] or file_dict['edw_error_message'] ):
        file_dict['status'] = 'OBSOLETE'
    else:
        file_dict['status'] = 'CURRENT'

    del file_dict['lab_error_message']
    del file_dict['edw_error_message']
     # hide assembly for fastQ's -- (EDW retains it to represent organism)
    if file_dict['file_format'] in ['fasta', 'fastq']:
        del file_dict['assembly']

    resp = app.get('/users/'+file_dict['submitted_by'],headers={'Accept': 'application/json'}).maybe_follow()
    if not file_dict['submitted_by'] or resp.status_code != 200:
        logger.error('EDW submitter %s cannot be found' % file_dict['submitted_by'])
    else:
        file_dict['submitted_by'] = resp.json['@id']

    try:
        ds_acc = file_dict['dataset']
    except:
        logger.error('EDW file %s has no dataset' % file_dict['accession'])
        ds_acc = None

    if ds_acc:
        ds = get_dataset_or_experiment(app, ds_acc, phase)
        if not ds:
            logger.warning('EDW file %s has a dataset that cannot be found or is from wrong phase: %s' % (file_dict['accession'], ds_acc))
            file_dict = { 'accession': ""}
        else:
            file_dict['dataset'] = ds['@id']
            if ds['dataset_type'] == 'experiment':
                file_dict['replicate'] = find_replicate(ds, file_dict)

    if file_dict.has_key('replicate') and file_dict['replicate']:
        del file_dict['biological_replicate']
        del file_dict['technical_replicate']
            # otherwise we will try tor create the specified one.

    if file_dict.has_key('assembly'):
        ## HACK - these should not be allowed in EDW
        file_dict['assembly'] = re.sub(r'(male\.|female\.)', '', file_dict['assembly'])

    return file_dict  ## really don't convert None to unicode...
    #return { i : unicode(j) for i,j in file_dict.items() }


def find_replicate(experiment, file_dict):

    ''' special cases if no technical replicate:
    '''

    bio_rep = file_dict['biological_replicate']
    tech_rep = file_dict['technical_replicate']

    if not bio_rep or bio_rep == 'pooled' or bio_rep == 'n/a':
        # expected in some cases
        return None

    matches = []
    if tech_rep:
        matches = [ rep for rep in experiment['replicates']
          if rep['biological_replicate_number'] == int(bio_rep) and
             rep['technical_replicate_number'] == int(tech_rep)]
    else:
        logger.warn("No tech replicate specified for %s; experiment: %s (%s, %s) %s - trying for 1" %
          (file_dict['accession'], experiment['accession'], bio_rep, tech_rep, file_dict['output_type']))

        matches = [ rep for rep in experiment['replicates']  if rep['biological_replicate_number'] == int(bio_rep) ]
    if len(matches) == 1:
        return matches[0]['@id']
    else:
        logger.warn("Experiment %s (%s %s) matches %s replicates" % (experiment['accession'], bio_rep, tech_rep, len(matches)))
        return None


def get_dataset_or_experiment(app, accession, phase=edw_file.ENCODE_PHASE_ALL):
    # Map ENCODE2 experiment accession to ENCODE3
    # This will fail if EDW references a dataset instead of expt.

    global datasets
    global experiments
    global encode2_to_encode3
    global encode3_to_encode2

    lookup = encode2_to_encode3.get(accession, [])
    if len(lookup) > 1:
        logger.error("Encode2 dataset %s maps to multiple: (%s)" % (accession, lookup))
        return None
    elif lookup:
        ec3_acc = lookup.pop()
        logger.info("Encode2 dataset %s maps to %s " % (accession, ec3_acc))
        lookup.add(ec3_acc)
    else:
        ec3_acc = accession

    if (encode3_to_encode2.get(ec3_acc, []) and phase == edw_file.ENCODE_PHASE_3 ):
        logger.info("Dataset %s is not from phase %s" % (ec3_acc, phase))
        return None

    url = '/' + ec3_acc + '/'
    try:
        resp = app.get(url).maybe_follow()
    except AppError:
        logger.error("Dataset/Experiment %s could not be found." % ec3_acc)
        return None

    if resp.status_code == 200:
        return resp.json
    else:
        return None


def get_missing_filelist_from_lists(app_accs, edw_accs):
    # Find 'new' file accessions: files in EDW having experiment accesion
    #   but missing in app
    new_accs = []
    for accession in edw_accs:
        if accession not in app_accs:
            new_accs.append(accession)
    return new_accs


def get_app_fileinfo(app, phase=edw_file.ENCODE_PHASE_ALL):
    # Get file info from encoded web application
    # Return list of fileinfo dictionaries
    rows = get_collection(app, FILES)
    app_files = []
    for row in sorted(rows, key=itemgetter('accession')):
        url = row['@id']
        resp = app.get(url).maybe_follow()
        fileinfo = resp.json
        # below seems clunky, could search+filter
        if phase != edw_file.ENCODE_PHASE_ALL:
            file_phase = get_phase(app, fileinfo['dataset'])
            if file_phase != phase:
                    logging.info("File %s is wrong phase (%s)" % (fileinfo['accession'], file_phase))
                    continue
        app_files.append(resp.json)
    return app_files


def get_phase(app, ds_url):

    global encode3_to_encode2

    url2acc = re.compile('\/(experiments|datasets)\/(ENCSR.{6})\/')
    acc = url2acc.match(ds_url).group(2)

    try:
        if encode3_to_encode2[acc]:
            return edw_file.ENCODE_PHASE_2
        return edw_file.ENCODE_PHASE_3
    except:
        import pdb;pdb.set_trace()


def create_replicate(app, exp, bio_rep_num, tech_rep_num, dry_run=False):

    # create a replicate
    logger.warning("Creating replicate %s %s for %s" % (bio_rep_num, tech_rep_num, exp))
    rep = {
        'experiment': exp,
        'biological_replicate_number': bio_rep_num,
        'technical_replicate_number': tech_rep_num
    }

    logger.info('....POST replicate %d - %d for experiment %s' % (bio_rep_num, tech_rep_num, exp))
    url = collection_url(REPLICATES)
    if not dry_run:

        resp = app.post_json(url, rep)
        logger.info(str(resp))
        rep_id = str(resp.json[unicode('@graph')][0]['@id'])
        return rep_id

    else:
        return "/replicate/new"


def post_fileinfo(app, fileinfo, dry_run=False):
    # POST file info dictionary to open app

    accession = fileinfo['accession']

    logger.info('....POST file: %s' % (accession))
    logger.info("%s" % fileinfo)

    if accession == 'ENCFF001MXG':
        import pdb;pdb.set_trace()
    ds = fileinfo.get('dataset', None)
    dataset = None
    if ds:
        try:
            ds_resp = app.get('/'+ds).maybe_follow()
        except AppError, e:
            logger.error("Refusing to POST file with invalid dataset: %s (%s)" % (ds, e))
            return None
        else:
            dataset = ds_resp.json
    rep = fileinfo.get('replicate', None)


    if ds:
        if ( (dataset and dataset.get('@type', [])[0] == 'dataset' ) or
           ( not rep and not fileinfo['biological_replicate'] and not fileinfo['technical_replicate'] and
             (fileinfo['file_format'] != 'fastq' or fileinfo['file_format'] != 'bam')) ):
            # dataset primary files have irrelvant replicate info
            # non fastq non bam files can have no replicate specified
            del fileinfo['biological_replicate']
            del fileinfo['technical_replicate']
            fileinfo.pop('replicate', None)
        elif not rep:
            # try to create one
            try:
                br = int(fileinfo['biological_replicate'])
                tr = int(fileinfo['technical_replicate'])
                fileinfo['replicate'] = create_replicate(app, ds, br, tr, dry_run)
                del fileinfo['biological_replicate']
                del fileinfo['technical_replicate']
            except ValueError, e:
                logger.error("Refusing to POST file %s with confusing replicate ids: (%s, %s)" %
                   (fileinfo['accession'], fileinfo['biological_replicate'], fileinfo['technical_replicate']))
                logger.info("%s" % e)
                return None
            except KeyError, e:
                logger.error("Refusing to POST file %s with missing replicate ids: (%s, %s)" %
                   (fileinfo['accession'], fileinfo['biological_replicate'], fileinfo['technical_replicate']))
                logger.info("%s" % e)
                return None
            except AppError, e:
                logger.error("Can not POST this replicate because reasons: %s" % e.message)
                return None
            except Exception, e:
                logger.error("Something untoward (%s) happened trying to create replicates: for %s" % (e, fileinfo))
                sys.exit(1)


    url = collection_url(FILES)
    if not dry_run:
        resp = app.post_json(url, fileinfo, expect_errors=True)  #?collection_sourcd=db
        logger.info(str(resp) + "")
        if resp.status_int == 409:
            logger.warning('Failed POST File %s: File already exists', accession)
        elif resp.status_int < 200 or resp.status_int >= 400:
            logger.error('Failed POST File %s%s', accession, resp)
        else:
            logger.info('Successful POST File: %s' % (accession))
        return resp
    else:
        logger.debug('Sucessful dry-run POST File %s' % (accession))
        return {'status_int': 201}


def get_dicts(app, edw, phase=edw_file.ENCODE_PHASE_ALL):

    edw_files = edw_file.get_edw_fileinfo(edw, phase=phase)
    # Other parameters are default
    edw_dict = { d['accession']:convert_edw(app, d, phase) for d in edw_files }
    app_files = get_app_fileinfo(app, phase=phase)
    app_dict = { d['accession']:d for d in app_files }

    return edw_dict, app_dict


def get_all_datasets(app, phase=edw_file.ENCODE_PHASE_ALL):

    global experiments
    global datasets
    global encode2_to_encode3
    global encode3_to_encode2

    logger.info("Getting all experiments...")
    exp_collection = get_collection(app, EXPERIMENTS)
    logger.warn("Found %s experiments" % len(exp_collection))

    for exp in exp_collection:
        dbxrefs = exp[ENCODE2_EXP_PROP]
        acc = exp['accession']
        for xref in dbxrefs:
            e2e3 = encode2_to_encode3.get(xref, set())
            e2e3.add(acc)
            encode2_to_encode3[xref] = e2e3

        e3e2 = encode3_to_encode2.get(acc,set())
        e3e2.update(set(dbxrefs))
        encode3_to_encode2[acc] = e3e2

        #experiments[acc] = app.get(exp['@id']).maybe_follow().json
        # should lazy load them, we won't always need them all
        # we do need the replicates however
        experiments[acc] = []


    logger.info("Getting all datasets...")
    alias_key = re.compile('ucsc_encode_db:')
    ds_collection = get_collection(app, DATASETS)
    logger.warn("Found %s datasets" % len(ds_collection))

    for ds in ds_collection:
        acc = ds['accession']
        aliases = ds[ENCODE2_DS_PROP]
        for al in aliases:
            if not alias_key.match(al):
                dbxref = alias_key.sub('', al)

                e2e3 = encode2_to_encode3.get(xref, [])
                e2e3.append(acc)
                encode2_to_encode3[xref] = e2e3

                e3e2 = encode3_to_encode2.get(acc,[])
                e3e2.append(dbxref)
                encode3_to_encode2[acc] = e3e2

        datasets[acc] = ds
        ## don't need replicates or anything.
    logger.warn("%s Encode2 experiments can be referenced" % len(encode2_to_encode3.keys()))

def patch_fileinfo(app, props, propinfo, dry_run=False):
    # PATCH properties to file in app

    #TODO: handle this case in test:
    #webtest.app.AppError: Bad response: 422 Unprocessable Entity (not one of 200, 201, 409 for http://localhost/files/ENCFF001MXD)
    #{"status": "error", "errors": [{"location": "body", "name": ["replicate"], "description": "None is not of type u'string'"}, {"location": "body", "name": [], "description": "Additional properties are not allowed (u'technical_replicate', u'biological_replicate' were unexpected)"}], "description": "Failed validation", "title": "Unprocessable Entity", "code": 422, "@type": ["ValidationFailure", "error"]}


    accession = propinfo['accession']

    logger.info('....PATCH file: %s' % (accession))
    for prop in props:
        if prop in NO_UPDATE:
            logger.error('Refusing to PATCH %s (%s): for %s' % (prop, propinfo[prop], accession))
            return None

    url = collection_url(FILES) + accession
    if not dry_run:
        resp = app.patch_json(url, propinfo, status=[200, 201, 409, 422])
        logger.info(str(resp))
        if resp.status_int < 200 or resp.status_int == 409:
            logger.error('Failed PATCH File %s%s', accession, resp)
            return None
        else:
            logger.info('Successful PATCH File: %s' % (accession))
            return resp
    else:
        logger.debug('Sucessful dry-run PATCH File %s' % (accession))
        return {'status_int': 201}


def collection_url(collection):
    # Form URL from collection name
    return '/' + collection + '/'


def get_collection(app, collection):
    # GET JSON objects from app as a list
    # NOTE: perhaps limit=all should be default for JSON output
    # and app should hide @graph (provide an iterator)
    url = collection_url(collection)
    url += "?limit=all&collection_source=db"
    resp = app.get(url)
    return resp.json['@graph']


def compare_files(aa, bb):
    a_keys = set(aa.keys())
    b_keys = set(bb.keys())
    intersect_keys = a_keys.intersection(b_keys)
    a_only_keys = a_keys - b_keys
    b_only_keys = b_keys - a_keys

    modified = { o : (aa[o], bb[o]) for o in intersect_keys if aa[o] != bb[o] }
    return modified


def internal_app(configfile, username=''):
    app = get_app(configfile)
    if not username:
        username = IMPORT_USER
    environ = {
        'HTTP_ACCEPT': 'application/json',
        'REMOTE_USER': username,
    }
    return TestApp(app, environ)


def make_app(application, username, password):
    # Configure test app
    logger.info('Using encoded app: %s' % application)


    app = internal_app(application, username)

    # check schema version
    resp = app.get(FILE_PROFILE_URL)
    schema = resp.json['properties']['schema_version']['default']
    if schema != FILE_SCHEMA_VERSION:
        logger.error('ERROR: File schema has changed: is %s, expecting %s' %
                        (schema, FILE_SCHEMA_VERSION))
        sys.exit(1)
    return app


def inventory_files(app, edw_dict, app_dict):
    # Inventory files
    edw_only = []
    app_only = []
    same = []
    diff_accessions = []

    for accession in sorted(edw_dict.keys()):
        edw_fileinfo = edw_dict[accession]
        if not edw_fileinfo['accession']:
            continue
            # most likely dropped out due to wrong phase
        if accession not in app_dict:
            edw_only.append(edw_fileinfo)
        else:
            diff = compare_files(edw_fileinfo, app_dict[accession])
            if diff:
                diff_accessions.append(accession)
                logger.warning("File: %s has %s DIFFS (edw, encoded):" % (accession, diff))
            else:
                same.append(edw_fileinfo)

    # APP-only files
    for accession in sorted(app_dict.keys()):
        if accession not in edw_dict:
            app_only.append(app_dict[accession])

    return edw_only, app_only, same, diff_accessions


def run(app, app_files, edw_files, phase=edw_file.ENCODE_PHASE_ALL, dry_run=False):


    edw_only, app_only, same, patch = inventory_files(app, edw_files, app_files)
    logger.warn("Comparison")
    logger.warn("=================")
    logger.warn("%s files in EDW only" % len(edw_only))
    logger.warn("%s files in encoded only" % len(app_only))
    logger.warn("%s files are identical" % len(same))
    logger.warn("%s files need to be patched" % len(patch))

    for add in edw_only:
        acc = add['accession']
        url = collection_url(FILES) + acc
        resp = post_fileinfo(app, add, dry_run)

    for update in patch:
        diff = compare_files(app_files[update], edw_files[update])
        patched = patch_fileinfo(app, diff.keys(), edw_files[update], dry_run)



def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Synchronize EDW and encoded files/replicates", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--dry-run', action='store_true', help="Don't post or patch, just report")
    parser.add_argument('-d', '--data_host', default=None,
                        help='data warehouse host (default from my.cnf)')
    parser.add_argument('-a', '--config_uri', default=DEFAULT_INI,
                    help='application url or .ini (default %s)' % DEFAULT_INI)
    parser.add_argument('-u', '--username', default='',
                    help='HTTP username (access_key_id) or import user')
    parser.add_argument('-p', '--password', default='',
                        help='HTTP password (secret_access_key)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='verbose mode')

    parser.add_argument('-P', '--phase',
                            choices=[edw_file.ENCODE_PHASE_2,
                                     edw_file.ENCODE_PHASE_3,
                                     edw_file.ENCODE_PHASE_ALL],
                            default=edw_file.ENCODE_PHASE_ALL,
                    help='restrict EDW files by ENCODE phase accs (default %s)' % edw_file.ENCODE_PHASE_ALL)

    args = parser.parse_args()

    FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
    logging.basicConfig(format=FORMAT)
    logger.setLevel(logging.WARNING)

    if args.verbose:
        logger.setLevel(logging.INFO)


    app = make_app(args.config_uri, args.username, args.password)

    get_all_datasets(app)

    edw = edw_file.make_edw(args.data_host)

    edw_files, app_files = get_dicts(app, edw, phase=args.phase)

    logger.warning("Found %s files at encoded; %s files at EDW" % (len(app_files), len(edw_files)))
    if args.phase != edw_file.ENCODE_PHASE_ALL:
        logger.warning("Synching files from Phase %s only" % args.phase)

    return run(app, app_files, edw_files, phase=args.phase, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
