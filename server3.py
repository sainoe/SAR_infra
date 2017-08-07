from flask import Flask, url_for, request, Response, render_template
import os, time, json, boto, boto.s3.connection, operator
import requests
from pprint import pprint as pp
from slipstream.api import Api
from datetime import datetime
from threading import Thread
import lib_access as la
import decision_making_module as dmm
import summarizer_final as sumarizer
import numpy as np
# -*- coding: utf-8 -*-
app = Flask(__name__)
api = Api()
elastic_host = 'http://localhost:9200'
doc_type = '/foo3/'

@app.route('/')
def form():
    return render_template('form_submit.html')


def connect_s3():
    access_key = "EXOb02927b095f5f60382e5513e"
    secret_key = "vL3PGh4fiPBNb5L4QNIatRyy2xSV8JkfCiCIum_dZJA"
    host       = "sos.exo.io"

    conn = boto.connect_s3(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        host=host,
        #is_secure=False,               # uncomment if you are not using ssl
        calling_format = boto.s3.connection.OrdinaryCallingFormat(),
        )
    return(conn)

def _format_specs(specs):
    for k,v in specs.items():
        specs[k][0] = ("resource:vcpu='%d'" % v[0])
        specs[k][1] = ("resource:ram>'%d'" % v[1])
        specs[k][2] = ("resource:disk>'%d'" % v[2])

    return specs

def get_BDB(case):

    specs_ex = ["resource:vcpu='4'",
                       "resource:ram>'15000'",
                       "resource:disk>'100'",
                       "resource:operatingSystem='linux'"]
    specs_ex2 = ["resource:vcpu='4'",
                       "resource:ram>'15000'",
                       "resource:disk>'100'",
                       "resource:operatingSystem='linux'"]
    BDB = {

     'case1': {'run_a': ['c1',
                'service-offer/2d225047-9068-4555-81dd-d288562a57b1',
                500],
                     'run_b': ['c2',
                'service-offer/a264258f-b6fe-4f3e-b9f4-3722b6a1c6c7',
                 500],
                 'run_c': ['c2',
                 'service-offer/a264258f-b6fe-4f3e-b9f4-3722b6a1c6c7',
                 600],
                'run_d': ['c1',
                'service-offer/a264258f-b6fe-4f3e-b9f4-3722b6a1c6c7',
                650],
                'run_d': ['c4',
                'service-offer/a264258f-b6fe-4f3e-b9f4-3722b6a1c6c7',
                650]},

    'case2' : {},
    }
    return (BDB[case])


def apply_time_filter(BDB, t):
    return({k:v for k,v in BDB.iteritems() if v[2] <= t})


def apply_cloud_filter(BDB, c):
    return({k:v for k,v in BDB.iteritems() if v[0] in c})


def apply_filter_BDB(BDB, c, t):
    return(apply_time_filter(apply_cloud_filter(BDB,c),t))


def get_price(id):
    return(api.cimi_get(id).json['price:unitCost'])


def get_vm_specs(id):
    json = api.cimi_get(id).json
    spec_keys = ['id',
                 'resource:vcpu',
                 'resource:ram',
                 'resource:disk']
                 #'resource:typeDisk'] Maybe SSD boost the process
    return(tuple(v for k,v in json.items() if k in spec_keys))


def rank_per_price_BDB(BDB):
    temp = [(v[1], get_price(v[1])) for k,v in BDB.items()]
    temp.sort(key=lambda tuple: tuple[1])
    return(temp)


def rank_per_resource(list_id_res):
    list_id_res.sort(key=lambda tuple: tuple[1])
    return(temp[0])


def check_vm_specs(vm_ids):
    print "CHECK SPECS"
    vm_specs   = map(get_vm_specs, vm_ids)
    return(compare_vm_specs(vm_specs))


def compare_vm_specs(vm_specs):
    dtype = 'i8, i8, |S64 , i8'
    vm_specs = np.array(vm_specs, dtype=dtype)

    return(vm_specs[0][2])


def choose_vm(vm_set):
    best_price = vm_set[0][1]
    best_vms   = [k for k,v in vm_set if v == best_price]

    if len(best_vms) > 1:
        my_vm = check_vm_specs(best_vms)
    else:
        my_vm = best_vms[0]
    return my_vm


def DMM(clouds, time, offer):
    """
    Lookup for runs in benchmarking DB which :

        - were deployed on the clouds where the data is located

        - have an execution equal or smaller than the SLA

        - The cheapest

        - with the best specs

    """
    # BDB_temp = apply_filter_BDB(BDB, clouds, 500 )
    # vm_set   = rank_per_price_BDB(BDB_temp)
    # my_vm    = choose_vm(vm_set)

    ranking = dmm.dmm(clouds, time, offer)
    pp(ranking)
    return(ranking)


def download_product(bucket_id, conn, output_id):
    """
    :param   bucket_id: uri of the bucket
    :type    bucket_id: str

    :param   conn: interface to s3 bucket
    :type    conn: boto connect_s3 object

    param    output_id: product id
    type     output_id: str
    """

    bucket      = conn.get_bucket(bucket_id)
    key         = bucket.get_key(output_id)
    output_path = os.getcwd() + output_id
    key.get_contents_to_filename(output_path)

    print "Product stored @ %s." % output_id


def cancel_deployment(deployment_id):
    api.terminate(deployment_id)
    state = api.get_deployment(deployment_id)[2]
    while state != 'cancelled':
        print "Terminating deployment %s." % deployment_id
        time.sleep(5)
        api.terminate(deployment_id)


def watch_execution_time(start_time):
    time_format = '%Y-%m-%d %H:%M:%S.%f UTC'
    delta = datetime.utcnow() - datetime.strptime(start_time,
                                            time_format)
    execution_time = divmod(delta.days * 86400 + delta.seconds, 60)
    return(execution_time)


def wait_product(deployment_id, cloud, time_limit):
    """
    :param   deployment_id: uuid of the deployment
    :type    deployment_id: str
    """
    deployment_data = api.get_deployment(deployment_id)
    state           = deployment_data[2]
    output_id       =  ""

    while state != "ready" and  not output_id:
        deployment_data = api.get_deployment(deployment_id)
        t = watch_execution_time(deployment_data[3])
        print "Waiting state ready. Currently in state : \
                 %s Time elapsed: %s mins, seconds" % (state, t)

        if (t[0]* 60 + t[1]) >= time_limit:
            cancel_deployment(deployment_id)
            return("SLA time bound exceeded. Deployment is cancelled.")

        time.sleep(45)
        state = deployment_data[2]
        output_id = deployment_data[8].split('/')[-1]

    conn = connect_s3()
    download_product("eodata_output2", conn, output_id)
    summarizer.summarize_run(deployment_id, cloud)

    return("Product %s delivered!" % outpud_id)


def _all_products_on_cloud(c, rep_so, prod_list):
    print c
    print prod_list
    products_cloud = ['xXX' for so in rep_so if so['connector']['href'] == c]

    return len(products_cloud) == len(prod_list)

def _check_str_list(data):
	if isinstance(data, unicode) or isinstance(data, str):
		data = [data]
	return data

def find_data_loc(prod_list):
    """
    :param   prod_list: Input product list
    :type    prod_list: list

    :param   cloud_legit: Data localization found on service catalog
    :type    cloud_legit: dictionnary
    """
    prod_list = _check_str_list(prod_list)
    specs_data         = ["resource:type='DATA'", "resource:platform='S3'"]
    rep_so             = la.request_data(specs_data, prod_list)['serviceOffers']
    cloud_set      = list(set([c['connector']['href'] for c in rep_so]))
    #cloud_set      = []
    #['cloud_a, cloud_b, cloud_c', 'cloud_d']
    cloud_legit    = []
    #cloud_legit    = ['c1', 'c2', 'c3', 'c4'] # FAKED

    for c in cloud_set:
        if _all_products_on_cloud(c, rep_so, prod_list):
             cloud_legit.append(c)
    _check_str_list(cloud_legit)
    return(cloud_legit)


def _schema_validation(jsonData):
    """
    Input data Schema:
    - A JSON with top hierarchy 'SLA' and 'results' dicts:

    jsonData = {'SLA':dict, 'result':dict}

    dict('SLA')    = {'requirements':['time','price', 'resolution'], 'order':['prod_list']}
    dict('result') = {''}
    """
    if not "SLA" in jsonData:
        raise ValueError("No 'SLA' in given data")
    if not "result" in jsonData:
        raise ValueError("No 'result' in given data")
    for k,v in jsonData.items():
        if not isinstance(v, dict):
            raise ValueError("%s is not a dict in given data" % k)

    SLA = jsonData['SLA']

    if not "product_list" in SLA:
        raise ValueError("Missing product list in given SLA data")
    if not "requirements" in SLA:
        raise ValueError("Missing requirements in given SLA data")

    for k,v in jsonData['SLA'].items():
        if not isinstance(v, list):
            raise ValueError("%s is not a list in given data" % k)

    return True


def populate_db( index, type, id=None):
      request = elastic_host + index + type + id
      rep = res.indices.create(request, ignore=400)

      return rep


def create_BDB(clouds, specs_vm):
    index='/sar/'
    type='/offer-cloud/'
    req_index = requests.get(elastic_host + index)

    if not req_index:
        populate_db( index, type)

    for c in clouds:
        rep = populate_db( index, type, c)
        serviceOffers = _components_service_offers(c, specs_vm)
        benchmarks = deploy_run(c, product, serviceOffers, 9999)
        print rep

def _check_BDB_cloud(index, clouds):
    valid_cloud = []
    for c in _check_str_list(clouds):
        rep = _get_elastic(index + doc_type + '%s/' % c)
        if rep.json()['found']:
            valid_cloud.append(c)

    if not valid_cloud:
        raise ValueError("Benchmark DB has no logs for %s \
                        go use POST on `SLA_INIT` to initialize." % clouds)
    return valid_cloud


def _get_elastic(index=""):
    return requests.get(elastic_host + index)

def _check_BDB_state():
    if not _get_elastic():
        raise ValueError("Benchmark DB down!")
    return True

def _check_BDB_index(index):
    _check_BDB_state()
    rep_index = _get_elastic(index)
    if (not rep_index) or (len(rep_index.json()) < 1):
        raise ValueError("Empty Benchmark DB please use POST on `SLA_INIT` \
                                        to initialize the system")
    return True

def _request_validation(request):
    if request.method == 'POST':
        _schema_validation(request.get_json())
    else:
        raise ValueError("Not a POST request")
    return True


def _components_service_offers(cloud, specs):
    cloud = [("connector/href='%s'" % cloud)]
    serviceOffers = { 'mapper': la.request_vm(specs['mapper'], cloud),
                      'reducer': la.request_vm(specs['reducer'], cloud) }
    return serviceOffers

def deploy_run(data_loc, product, serviceOffers, time ):
    mapper_so =  serviceOffers['mapper']['serviceOffers']
    reducer_so =  serviceOffers['reducer']['serviceOffers']
    rep = ""
    if mapper_so and reducer_so:
        print mapper_so[0]['id']
        print reducer_so[0]['id']
        deploy_id = api.deploy('EO_Sentinel_1/procSAR',
                cloud={'mapper': c, 'reducer':c},
                parameters={'mapper' : {'service-offer': \
                             mapper_so[0]['id'],
                             'product-list':product},
                             'reducer': {'service-offer': \
                             reducer_so[0]['id']}},
                tags='EOproc', keep_running='never')

        daemon_watcher = Thread(target = wait_product, args = (deploy_id, c, time))
        daemon_watcher.setDaemon(True)
        daemon_watcher.start()
        rep += rep
    else:
        print("No corresponding instances type found on connector %s" % c)
return rep

@app.route('/SLA_TEST', methods=['POST'])
def sla_test():
    print request.get_json()
    return "check"

''' initialization from the system admin :

    : Inputs specs and products

    : Verify if the DB is on
    : Find the connector to cloud where the
    data is localized

    : Run the benchmark
    : Populate the DB

    input = { product: "",
              specs_vm: {'mapper': la.request_vm(specs['mapper'], cloud),
                      'reducer': a.request_vm(specs['reducer'], cloud)}


'''
@app.route('/SLA_INIT', methods=['POST'])
def sla_init():
   data = request.get_json()
   product = data['product']
   specs_vm   = _format_specs(data['specs_vm'])
   print specs_vm

   try:
       _check_BDB_state()
       data_loc   = find_data_loc(product)
       print data_loc
       if not data_loc :
           raise ValueError("The data has not been found in any connector \
                             associated with the Nuvla account")
       print "Data located in: %s" % data_loc
       create_BDB(data_loc, specs_vm)
       msg = "Cloud %s currently benchmarked." % benchmarks
       status = "201"

   except ValueError as err:
       msg = "Value error: {0} ".format(err)
       status = "404"
       print("Value error: {0} ".format(err))

   resp = Response(msg, status=status, mimetype='application/json')
   resp.headers['Link'] = 'http://sixsq.eoproc.com'
   return resp


@app.route('/SLA_CLI', methods=['POST'])
def sla_cli():
    index = '/sar7'

    try:
        _check_BDB_index(index)
        _request_validation(request)
        data = request.get_json()
        sla = data['SLA']
        pp(sla)
        product_list  =  sla['product_list']
        time  = sla['requirements'][0]
        offer = sla['requirements'][1]
        data_loc   = find_data_loc(product_list)
        print "Data located in: %s" % data_loc

        data_loc   = _check_BDB_cloud(index, data_loc)
        msg    = ""
        status = ""

        if data_loc:
            msg    = "SLA accepted! "
            status = "201"
            time = 500
            ranking = dmm.dmm(data_loc, time, offer)
            pp(ranking)
            serviceOffers = { 'mapper': ranking[1],
                              'reducer': ranking[2]}
            deploy_run(data_loc, product_list, serviceOffers, time) # offer

        else:
            msg = "Data not found in clouds!"
            status = 412


    except ValueError as err:
        msg = "Value error: {0} ".format(err)
        status = "404"
        print("Value error: {0} ".format(err))

    resp = Response(msg, status=status, mimetype='application/json')
    resp.headers['Link'] = 'http://sixsq.eoproc.com'
    return resp

if __name__ == '__main__':
    api.login('simon1992', '12mc0v2ee64o9')
    app.run(
        host="0.0.0.0",
        port=int("80")
)
