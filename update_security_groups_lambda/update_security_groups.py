import boto3
from distutils.util import strtobool
import hashlib
import json
import logging
import os
import urllib.request, urllib.error, urllib.parse

logger = logging.getLogger(__name__)


# Name of the service, as seen in the ip-groups.json file, to extract information for
SERVICE = "EC2"
# Ports your application uses that need inbound permissions from the service for
INGRESS_PORTS = { 'Http': 80, 'Https': 443 }
# Tags which identify the security groups you want to update
TAGS = { 
    'Name': os.environ.get('TagName', 'Name'),
    'AutoUpdate': os.environ.get('TagAutoUpdate', 'AutoUpdate'),
    'Protocol': os.environ.get('TagProtocol', 'Protocol')
}

SECURITY_GROUP_TAG_FOR_GLOBAL_HTTP = { TAGS['Name']: 'EC2_g', TAGS['AutoUpdate']: 'true', TAGS['Protocol']: 'http' }
SECURITY_GROUP_TAG_FOR_GLOBAL_HTTPS = { TAGS['Name']: 'EC2_g', TAGS['AutoUpdate']: 'true', TAGS['Protocol']: 'https' }
SECURITY_GROUP_TAG_FOR_REGION_HTTP = { TAGS['Name']: 'EC2_r', TAGS['AutoUpdate']: 'true', TAGS['Protocol']: 'http' }
SECURITY_GROUP_TAG_FOR_REGION_HTTPS = { TAGS['Name']: 'EC2_r', TAGS['AutoUpdate']: 'true', TAGS['Protocol']: 'https' }


logger.setLevel(logging.INFO)
try:
     if bool(strtobool(os.environ.get('DEBUG', ''))):
         logger.setLevel(logging.DEBUG)
except ValueError:
    pass


def lambda_handler(event, context):
    logger.debug("Received event: " + json.dumps(event, indent=2))
    event_sns = event['Records'][0]['Sns']
    logger.info("Received SNS event: " + event_sns['MessageId'])
    message = json.loads(event_sns['Message'])

    # Load the ip ranges from the url
    ip_ranges = json.loads(get_ip_groups_json(message['url'], message['md5']))

    # Extract the service ranges
    global_cf_ranges = get_ranges_for_service(ip_ranges, SERVICE, "GLOBAL")
    region_cf_ranges = get_ranges_for_service(ip_ranges, SERVICE, "REGION")
    ip_ranges = { "GLOBAL": global_cf_ranges, "REGION": region_cf_ranges }

    # Update the security groups
    result = update_security_groups(ip_ranges)

    return result


def get_ip_groups_json(url, expected_hash):
    logger.info("Updating from " + url)

    response = urllib.request.urlopen(url)
    ip_json = response.read()

    m = hashlib.md5()
    m.update(ip_json)
    hash = m.hexdigest()

    if hash != expected_hash:
        raise Exception('MD5 Mismatch: got ' + hash + ' expected ' + expected_hash)

    return ip_json


def get_ranges_for_service(ranges, service, subset):
    service_ranges = list()
    for prefix in ranges['prefixes']:
        if prefix['service'] == service and ((subset == prefix['region'] and subset == "GLOBAL") or (subset != 'GLOBAL' and prefix['region'] != 'GLOBAL')):
            logger.debug('Found ' + service + ' region: ' + prefix['region'] + ' range: ' + prefix['ip_prefix'])
            service_ranges.append(prefix['ip_prefix'])

    return service_ranges


def split_ranges(new_ranges, split_amount):
    total_len = len(new_ranges)
    for index in range(0, total_len, split_amount):
        yield new_ranges[index:index+split_amount]

def update_security_groups(new_ranges):
    client = boto3.client('ec2')

    global_http_group = get_security_groups_for_update(client, SECURITY_GROUP_TAG_FOR_GLOBAL_HTTP)
    global_https_group = get_security_groups_for_update(client, SECURITY_GROUP_TAG_FOR_GLOBAL_HTTPS)
    region_http_group = get_security_groups_for_update(client, SECURITY_GROUP_TAG_FOR_REGION_HTTP)
    region_https_group = get_security_groups_for_update(client, SECURITY_GROUP_TAG_FOR_REGION_HTTPS)

    logger.info('Found ' + str(len(global_http_group)) + ' EC2_g HttpSecurityGroups to update')
    logger.info('Found ' + str(len(global_https_group)) + ' EC2_g HttpsSecurityGroups to update')
    logger.info('Found ' + str(len(region_http_group)) + ' EC2_r HttpSecurityGroups to update')
    logger.info('Found ' + str(len(region_https_group)) + ' EC2_r HttpsSecurityGroups to update')

    result = list()
    global_http_updated = 0
    global_https_updated = 0
    region_http_updated = 0
    region_https_updated = 0
    split_amount = 59  # max ingress rules for sg (default 60)
    update_range = split_ranges(new_ranges["GLOBAL"], split_amount)
    for group in global_http_group:
        if update_security_group(client, group, next(update_range,None), INGRESS_PORTS['Http']):
            global_http_updated += 1
            result.append('Updated ' + group['GroupId'])
    update_range = split_ranges(new_ranges["GLOBAL"], split_amount)
    for group in global_https_group:
        if update_security_group(client, group, next(update_range, None), INGRESS_PORTS['Https']):
            global_https_updated += 1
            result.append('Updated ' + group['GroupId'])
    update_range = split_ranges(new_ranges["REGION"], split_amount)
    for group in region_http_group:
        if update_security_group(client, group, next(update_range, None), INGRESS_PORTS['Http']):
            region_http_updated += 1
            result.append('Updated ' + group['GroupId'])
    update_range = split_ranges(new_ranges["REGION"], split_amount)
    for group in region_https_group:
        if update_security_group(client, group, next(update_range, None), INGRESS_PORTS['Https']):
            region_https_updated += 1
            result.append('Updated ' + group['GroupId'])

    result.append('Updated ' + str(global_http_updated) + ' of ' + str(len(global_http_group)) + ' EC2_g HttpSecurityGroups')
    result.append('Updated ' + str(global_https_updated) + ' of ' + str(len(global_https_group)) + ' EC2_g HttpsSecurityGroups')
    result.append('Updated ' + str(region_http_updated) + ' of ' + str(len(region_http_group)) + ' EC2_r HttpSecurityGroups')
    result.append('Updated ' + str(region_https_updated) + ' of ' + str(len(region_https_group)) + ' EC2_r HttpsSecurityGroups')

    return result


def update_security_group(client, group, new_ranges, port):
    added = 0
    removed = 0

    if len(group['IpPermissions']) > 0:
        for permission in group['IpPermissions']:
            if permission['FromPort'] <= port and permission['ToPort'] >= port:
                old_prefixes = list()
                to_revoke = list()
                to_add = list()
                for range in permission['IpRanges']:
                    cidr = range['CidrIp']
                    old_prefixes.append(cidr)
                    if new_ranges.count(cidr) == 0:
                        to_revoke.append(range)
                        logger.debug(group['GroupId'] + ": Revoking " + cidr + ":" + str(permission['ToPort']))

                for range in new_ranges:
                    if old_prefixes.count(range) == 0:
                        to_add.append({ 'CidrIp': range })
                        logger.debug(group['GroupId'] + ": Adding " + range + ":" + str(permission['ToPort']))

                removed += revoke_permissions(client, group, permission, to_revoke)
                added += add_permissions(client, group, permission, to_add)
    else:
        to_add = list()
        for range in new_ranges:
            to_add.append({ 'CidrIp': range })
            logger.debug(group['GroupId'] + ": Adding " + range + ":" + str(port))
        permission = { 'ToPort': port, 'FromPort': port, 'IpProtocol': 'tcp'}
        added += add_permissions(client, group, permission, to_add)

    logger.info(group['GroupId'] + ": Added " + str(added) + ", Revoked " + str(removed))
    return (added > 0 or removed > 0)


def revoke_permissions(client, group, permission, to_revoke):
    if len(to_revoke) > 0:
        revoke_params = {
            'ToPort': permission['ToPort'],
            'FromPort': permission['FromPort'],
            'IpRanges': to_revoke,
            'IpProtocol': permission['IpProtocol']
        }

        client.revoke_security_group_ingress(GroupId=group['GroupId'], IpPermissions=[revoke_params])

    return len(to_revoke)


def add_permissions(client, group, permission, to_add):
    if len(to_add) > 0:
        add_params = {
            'ToPort': permission['ToPort'],
            'FromPort': permission['FromPort'],
            'IpRanges': to_add,
            'IpProtocol': permission['IpProtocol']
        }

        client.authorize_security_group_ingress(GroupId=group['GroupId'], IpPermissions=[add_params])

    return len(to_add)


def get_security_groups_for_update(client, security_group_tag):
    filters = list()
    for key, value in security_group_tag.items():
        filters.extend(
            [
                { 'Name': "tag-key", 'Values': [ key ] },
                { 'Name': "tag-value", 'Values': [ value ] }
            ]
        )

    response = client.describe_security_groups(Filters=filters)

    return response['SecurityGroups']


'''
Sample Event From SNS:

{
  "Records": [
    {
      "EventVersion": "1.0",
      "EventSubscriptionArn": "arn:aws:sns:EXAMPLE",
      "EventSource": "aws:sns",
      "Sns": {
        "SignatureVersion": "1",
        "Timestamp": "1970-01-01T00:00:00.000Z",
        "Signature": "EXAMPLE",
        "SigningCertUrl": "EXAMPLE",
        "MessageId": "95df01b4-ee98-5cb9-9903-4c221d41eb5e",
        "Message": "{\"create-time\": \"yyyy-mm-ddThh:mm:ss+00:00\", \"synctoken\": \"0123456789\", \"md5\": \"45be1ba64fe83acb7ef247bccbc45704\", \"url\": \"https://ip-ranges.amazonaws.com/ip-ranges.json\"}",
        "Type": "Notification",
        "UnsubscribeUrl": "EXAMPLE",
        "TopicArn": "arn:aws:sns:EXAMPLE",
        "Subject": "TestInvoke"
      }
    }
  ]
}

'''
