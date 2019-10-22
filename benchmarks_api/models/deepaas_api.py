# -*- coding: utf-8 -*-
"""
Model description
"""

import argparse
import pkg_resources
import yaml
import json
import os
import datetime
import requests

from tensorflow.python.client import device_lib
from werkzeug.exceptions import BadRequest
# import project's config.py
import benchmarks_api.config as cfg
import benchmark_cnn as benchmark
import cnn_util

# Info on available GPUs
local_gpus = []
num_local_gpus = 0
# Available models for the data sets
models_cifar10 = ('alexnet', 'resnet56', 'resnet110')
models_imagenet = ('alexnet', 'resnet50', 'resnet152', 'mobilenet', 'vgg16', 'vgg19', 'googlenet', 'overfeat', 'inception3')

time_fmt = '%Y-%m-%dT%H:%M:%S.%fZ'  # Timeformat of tf-benchmark


def get_metadata():
    """
    Function to read metadata
    """

    module = __name__.split('.', 1)

    pkg = pkg_resources.get_distribution(module[0])
    meta = {
        'Name': None,
        'Version': None,
        'Summary': None,
        'Home-page': None,
        'Author': None,
        'Author-email': None,
        'License': None,
    }

    for line in pkg.get_metadata_lines("PKG-INFO"):
        for par in meta:
            if line.startswith(par + ":"):
                _, value = line.split(": ", 1)
                meta[par] = value

    return meta


def predict_file(*args):
    """
    Function to make prediction on a local file
    """
    message = 'Not implemented in the model (predict_file)'
    return message


def predict_data(*args):
    """
    Function to make prediction on an uploaded file
    """
    message = 'Not implemented in the model (predict_data)'
    return message


def predict_url(*args):
    """
    Function to make prediction on a URL
    """
    message = 'Not implemented in the model (predict_url)'
    return message


###
# Uncomment the following two lines
# if you allow only authorized people to do training
###
# import flaat
# @flaat.login_required()
def train(train_args):
    """
    Train network
    train_args : dict
        Json dict with the user's configuration parameters.
        Can be loaded with json.loads() or with yaml.safe_load()    
    """

    run_results = {"status": "ok",
                   "user_args": {},
                   "machine_config": {},
                   "training": {},
                   "evaluation": {}
                   }

    run_results["user_args"] = train_args

    # Remove possible existing model and log files
    for f in os.listdir(cfg.MODEL_DIR):
        file_path = os.path.join(cfg.MODEL_DIR, f)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            print(e)

    # Load arguments
    kwargs = {'model': yaml.safe_load(train_args.model).split(' ')[0],
              'num_gpus': yaml.safe_load(train_args.num_gpus),
              'num_epochs': yaml.safe_load(train_args.num_epochs),
              'batch_size': yaml.safe_load(train_args.batch_size_per_device),
              'optimizer': yaml.safe_load(train_args.optimizer),
              'local_parameter_device': 'cpu',
              'variable_update': 'parameter_server'
              # 'log_dir': cfg.DATA_DIR,
              }

    # Locate training data and check if the selected network fits the
    if yaml.safe_load(train_args.dataset) != 'Synthetic data':
        kwargs['data_name'] = yaml.safe_load(train_args.dataset)
        kwargs['data_dir'] = cfg.DATA_DIR
        verify_selected_model(kwargs['model'], kwargs['data_name'])
    else:
        verify_selected_model(kwargs['model'], 'imagenet')

    # TODO: if cifar 10 is selected check, if it is mounted, else try to download it
    """cifar10_local = False
    if not cifar10_local:
        response = requests.get('https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz', stream=True)
        if response.status_code == 200:
            #with open(cfg.DATA_DIR, 'wb') as f:
            with open('/srv/benchmarks_api/data/test', 'wb') as f:
                f.write(response.raw.read())
        else:
            raise BadRequest('No local Cifar10 data set provided.\
                  But could not retrieve Cifar10 Data from "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz"!')
"""

    # If no GPU is available or the gpu option is set to 0 run CPU mode
    if num_local_gpus == 0 or kwargs['num_gpus'] == 0:
        kwargs['device'] = 'cpu'
        kwargs['data_format'] = 'NHWC'  # cpu data format
        kwargs['num_gpus'] = 1  # Important: tensorflow uses this also to specify the number of CPUs
    else:
        kwargs['device'] = 'gpu'
        kwargs['data_format'] = 'NCHW'


    # Append training info to run_result but not directories
    run_results["training"].update(kwargs)
    if run_results["training"]["device"] == "cpu":
        del run_results["training"]["num_gpus"]  # avoid misleading info
    kwargs['train_dir'] = cfg.MODEL_DIR
    kwargs['benchmark_log_dir'] = cfg.MODEL_DIR

    params = benchmark.make_params(**kwargs)


    # Setup and run the benchmark model
    try:
        params = benchmark.setup(params)
        bench = benchmark.BenchmarkCNN(params)
    except ValueError as param_ex:
        raise BadRequest("ValueError: {}".format(param_ex))

    tfversion = '.'.join([str(x) for x in cnn_util.tensorflow_version_tuple()])
    run_results["training"]["tf_version"] = tfversion

    # Run benchmark and measure total execution time
    bench.print_info()
    start_time_global = datetime.datetime.now().strftime(time_fmt)
    bench.run()
    end_time_global = datetime.datetime.now().strftime(time_fmt)


    # Read training and metric log files and get training results
    os.rename('{}/benchmark_run.log'.format(cfg.MODEL_DIR), '{}/training.log'.format(cfg.MODEL_DIR))
    training_file = '{}/training.log'.format(cfg.MODEL_DIR)
    run_parameters, machine_config = parse_logfile_training(training_file)
    run_results['training'].update(run_parameters)
    run_results["machine_config"] = machine_config

    metric_file = '{}/metric.log'.format(cfg.MODEL_DIR)
    run_results['training']['result'] = {}
    run_results['training']['result']['global_start_time'] = start_time_global
    run_results['training']['result']['global_end_time'] = end_time_global
    start, end, avg_examples = parse_metric_file(metric_file)
    run_results["training"]["result"]["average_examples_per_sec"] = avg_examples
    run_results['training']['result']['execution_start_time'] = start
    run_results['training']['result']['execution_end_time'] = end


    ## Evaluation ##
    if yaml.safe_load(train_args.evaluation):
        run_results["evaluation"] = {}

        kwargs_eval = {'model': kwargs['model'],
                       'num_gpus': kwargs['num_gpus'],
                       'device': kwargs['device'],
                       'data_format': kwargs['data_format'],
                       'benchmark_log_dir': kwargs['benchmark_log_dir'],
                       'train_dir': kwargs['train_dir'],
                       'eval': True
                       # 'eval_dir': cfg.DATA_DIR,
                       }
        run_results['evaluation']['device'] = kwargs_eval['device']
        if run_results['evaluation']['device'] == 'gpu':
            run_results['evaluation']['num_gpus'] = kwargs_eval['num_gpus']  # only for GPU to avoid confusion

        # Locate data
        if yaml.safe_load(train_args.dataset) != 'Synthetic data':
            kwargs_eval['data_name'] = kwargs['data_name']
            kwargs_eval['data_dir'] = kwargs['data_dir']

        # Setup and run the evaluation
        params_eval = benchmark.make_params(**kwargs_eval)
        try:
            params_eval = benchmark.setup(params_eval)
            evaluation = benchmark.BenchmarkCNN(params_eval)
        except ValueError as param_ex:
            raise BadRequest("ValueError: {}".format(param_ex))

        evaluation.print_info()
        start_time_global = datetime.datetime.now().strftime(time_fmt)
        evaluation.run()
        end_time_global = datetime.datetime.now().strftime(time_fmt)


        # Read log files and get evaluation results
        os.rename('{}/benchmark_run.log'.format(cfg.MODEL_DIR), '{}/evaluation.log'.format(cfg.MODEL_DIR))
        evaluation_file = '{}/evaluation.log'.format(cfg.MODEL_DIR)
        run_parameters = parse_logfile_evaluation(evaluation_file)
        run_results['evaluation'].update(run_parameters)

        logfile = '{}/metric.log'.format(cfg.MODEL_DIR)
        run_results['evaluation']['result'] = {}
        run_results['evaluation']['result']['global_start_time'] = start_time_global
        run_results['evaluation']['result']['global_end_time'] = end_time_global

        with open(logfile, "r") as f:
            for line in f:
                l = json.loads(line)
                if l["name"] == "eval_average_examples_per_sec":
                    run_results["evaluation"]['result']["average_examples_per_sec"] = l["value"]
                if l["name"] == "eval_top_1_accuracy":
                    run_results["evaluation"]['result']["top_1_accuracy"] = l["value"]
                if l["name"] == "eval_top_5_accuracy":
                    run_results["evaluation"]['result']["top_5_accuracy"] = l["value"]

    return run_results


def verify_selected_model(model, data_set):
    if data_set == 'cifar10':
        if model not in models_cifar10:
            raise BadRequest('Unsupported model selected! Cifar10 data set supported models are: {}'
                             .format(models_cifar10))
    if data_set == 'imagenet':
        if model not in models_imagenet:
            raise BadRequest('Unsupported model selected! ImageNet data set supported models are: {}'
                             .format(models_imagenet))


def parse_logfile_training(logFile):
    """ takes log file with benchmark settings in JSON format
        and parses relevant parts
    """
    run_parameters = {}
    with open(logFile, "r") as read_file:
        json_data = json.load(read_file)  # dictionary

        for el in json_data['run_parameters']:
            if el['name'] == 'batch_size':
                run_parameters['batch_size'] = el['long_value']
            if el['name'] == 'batch_size_per_device':
                run_parameters['batch_size_per_device'] = el['float_value']
            if el['name'] == 'num_batches':
                run_parameters['num_batches'] = el['long_value']

        machine_config = json_data["machine_config"]

    return run_parameters, machine_config


def parse_logfile_evaluation(logFile):
    """ takes log file with evaluation settings in JSON format
        and parses relevant parts
    """
    run_parameters = {}
    with open(logFile, "r") as read_file:
        json_data = json.load(read_file)  # dictionary

        for el in json_data['run_parameters']:
            if el['name'] == 'batch_size':
                run_parameters['batch_size'] = el['long_value']
            if el['name'] == 'batch_size_per_device':
                run_parameters['batch_size_per_device'] = el['float_value']
            if el['name'] == 'num_batches':
                run_parameters['num_batches'] = el['long_value']
            if el['name'] == 'data_format':
                run_parameters['data_format'] = el['string_value']
            # not sure why evaluation uses optimizer
            #if el['name'] == 'optimizer':
            #    run_parameters['optimizer'] = el['string_value']

    return run_parameters


def parse_metric_file(metric_file):
    """ takes the metric file and extracts timestamps and avg_imgs / sec info
    """
    with open(metric_file, "r") as f:
        maxStep = 0
        for line in f:
            el = json.loads(line)
            if el['name'] == "current_examples_per_sec" and el['global_step'] == 1:
                minTime = el['timestamp']
            if el['name'] == "current_examples_per_sec" and el['global_step'] > maxStep:
                maxTime = el['timestamp']
                maxStep = el['global_step']
            if el['name'] == 'average_examples_per_sec':
                avg_examples = el['value']
    return minTime, maxTime, avg_examples



def get_train_args():
    """
    Returns a dict of dicts to feed the deepaas API parser
    """
    train_args = cfg.train_args
    global local_gpus, num_local_gpus

    # convert default values and possible 'choices' into strings
    for key, val in train_args.items():
        val['default'] = str(val['default'])  # yaml.safe_dump(val['default']) #json.dumps(val['default'])
        if 'choices' in val:
            val['choices'] = [str(item) for item in val['choices']]


    # Adjust num_gpu option accordingly to available local devices
    local_devices = device_lib.list_local_devices()
    local_gpus = [x for x in local_devices if x.device_type == 'GPU']
    num_local_gpus = len(local_gpus)

    train_args['num_gpus']['choices'] = ['0']
    if num_local_gpus == 0:
        train_args['num_gpus']['default'] = '0'
    else:
        train_args['num_gpus']['default'] = '1'
        for i in range(num_local_gpus): train_args['num_gpus']['choices'].append(str(i+1))


    return train_args


# !!! deepaas>=0.5.0 calls get_test_args() to get args for 'predict'
def get_test_args():
    predict_args = cfg.predict_args

    # convert default values and possible 'choices' into strings
    for key, val in predict_args.items():
        val['default'] = str(val['default'])  # yaml.safe_dump(val['default']) #json.dumps(val['default'])
        if 'choices' in val:
            val['choices'] = [str(item) for item in val['choices']]

    return predict_args


# during development it might be practical
# to check your code from the command line
def main():
    """
       Runs above-described functions depending on input parameters
       (see below an example)
    """

    if args.method == 'get_metadata':
        get_metadata()
    elif args.method == 'train':
        train(args)
    else:
        get_metadata()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Model parameters')

    # get arguments configured for get_train_args()
    train_args = get_train_args()
    for key, val in train_args.items():
        parser.add_argument('--%s' % key,
                            default=val['default'],
                            type=type(val['default']),
                            help=val['help'])

    parser.add_argument('--method', type=str, default="get_metadata",
                        help='Method to use: get_metadata (default), \
                        predict_file, predict_data, predict_url, train')
    args = parser.parse_args()

    main()
