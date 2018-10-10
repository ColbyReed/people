#!/usr/bin/env python

import glob
import json
import os
import re
import sys
import uuid
import yaml
import yamlordereddictloader
from collections import defaultdict, OrderedDict
from utils import reformat_phone_number, reformat_address, get_data_dir, get_jurisdiction_id

# set up defaultdict representation
from yaml.representer import Representer
yaml.add_representer(defaultdict, Representer.represent_dict)


def dump_obj(obj, filename):
    with open(filename, 'w') as f:
        yaml.dump(obj, f, default_flow_style=False, Dumper=yamlordereddictloader.Dumper)


def process_dir(input_dir, output_dir, jurisdiction_id):
    memberships_by_id = defaultdict(list)
    memberships_by_name = defaultdict(list)
    # map org scrape IDs to org objects
    organizations = {}

    for filename in glob.glob(os.path.join(input_dir, 'organization_*.json')):
        with open(filename) as f:
            org = json.load(f)

        if org['classification'] == 'committee':
            organizations[org['_id']] = org = postprocess_org(org, jurisdiction_id)
        else:
            organizations[org['_id']] = org

    # resolve committee parents
    for org in organizations.values():
        if org['classification'] == 'committee':
            if org['parent'].startswith('~'):
                org['parent'] = json.loads(org['parent'][1:])['classification']
            filename = get_filename(org)
            dump_obj(org, os.path.join(output_dir, 'organizations', filename))

    for filename in glob.glob(os.path.join(input_dir, 'membership_*.json')):
        with open(filename) as f:
            membership = json.load(f)

        membership['organization'] = organizations.get(membership['organization_id'])
        if membership['person_id'].startswith('~'):
            memberships_by_name[membership['person_name']].append(membership)
        else:
            memberships_by_id[membership['person_id']].append(membership)

    for filename in glob.glob(os.path.join(input_dir, 'person_*.json')):
        with open(filename) as f:
            person = json.load(f)

        person['memberships'] = (memberships_by_id[person['_id']] +
                                 memberships_by_name[person['name']])
        person = postprocess_person(person, jurisdiction_id)
        filename = get_filename(person)
        dump_obj(person, os.path.join(output_dir, 'people', filename))


def get_filename(obj):
    id = obj['id']
    name = obj['name']
    name = re.sub('\s+', '-', name)
    name = re.sub('[^a-zA-Z-]', '', name)
    return f'{name}-{id}.yml'


def postprocess_link(link):
    if not link['note']:
        del link['note']
    return link


def postprocess_person(person, jurisdiction_id):
    optional_keys = (
        'image',
        'gender',
        'biography',
        'given_name',
        'family_name',
        'birth_date',
        'death_date',
        'national_identity',
        'summary',
        # maybe post-process these?
        'other_names',
    )

    result = OrderedDict(
        id=str(uuid.uuid4()),        # let's use uuid4 for these, override pupa's
        name=person['name'],
        party=[],
        roles=[],
        links=[postprocess_link(link) for link in person['links']],
        contact_details=[],
        # maybe post-process these?
        sources=[postprocess_link(link) for link in person['sources']],
        committees=[],
    )

    contact_details = defaultdict(lambda: defaultdict(list))
    for detail in person['contact_details']:
        value = detail['value']
        if detail['type'] in ('voice', 'fax'):
            value = reformat_phone_number(value)
        elif detail['type'] == 'address':
            value = reformat_address(value)
        contact_details[detail['note']][detail['type']] = value

    result['contact_details'] = [{'note': key, **val} for key, val in contact_details.items()]

    # memberships!
    for membership in person['memberships']:
        organization_id = membership['organization_id']
        if organization_id.startswith('~'):
            org = json.loads(organization_id[1:])
            if org['classification'] in ('upper', 'lower'):
                post = json.loads(membership['post_id'][1:])['label']
                result['roles'] = [
                    {'type': org['classification'], 'district': post,
                     'jurisdiction': jurisdiction_id}
                ]
            elif org['classification'] == 'party':
                result['party'] = [
                    {'name': org['name']}
                ]
        elif membership['organization']:
            result['committees'].append({
                'id': membership['organization']['id'],
            })
        else:
            raise ValueError(organization_id)

    for key in optional_keys:
        if person.get(key):
            result[key] = person[key]

    # promote some extras where appropriate
    extras = person.get('extras', {}).copy()
    for key in person.get('extras', {}).keys():
        if key in optional_keys:
            result[key] = extras.pop(key)
    if extras:
        result['extras'] = extras

    if person.get('identifiers'):
        result['other_identifiers'] = person['identifiers']

    return result


def postprocess_org(org, jurisdiction_id):
    return OrderedDict(
        id=str(uuid.uuid4()),        # let's use uuid4 for these, override pupa's
        name=org['name'],
        jurisdiction=jurisdiction_id,
        parent=org['parent_id'],
        classification=org['classification'],
        links=[postprocess_link(link) for link in org['links']],
        sources=[postprocess_link(link) for link in org['sources']],
    )


if __name__ == '__main__':
    input_dir = sys.argv[1]

    # abbr is last piece of directory name
    abbr = None
    for piece in input_dir.split('/')[::-1]:
        if piece:
            abbr = piece
            break

    output_dir = get_data_dir(abbr)
    jurisdiction_id = get_jurisdiction_id(abbr)

    for dir in ('people', 'organizations'):
        try:
            os.makedirs(os.path.join(output_dir, dir))
        except FileExistsError:
            for file in glob.glob(os.path.join(output_dir, dir, '*.yml')):
                os.remove(file)
    process_dir(input_dir, output_dir, jurisdiction_id)
