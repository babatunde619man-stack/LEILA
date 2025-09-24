import json

def get_owner_role_id():
    with open("config.json") as f:
        return int(json.load(f)["roles"]["owner"])

def get_staff_role_id():
    with open("config.json") as f:
        return int(json.load(f)["roles"]["staff"])

def get_buyer_role_id():
    with open("config.json") as f:
        return int(json.load(f)["roles"]["buyer"])

def get_buyer_vouch_channel_id():
    with open("config.json") as f:
        return int(json.load(f)["channels"]["buyer_vouch"])

def get_modlog_channel_id():
    with open("config.json") as f:
        return int(json.load(f)["channels"]["modlog"])

def get_transcript_channel_id():
    with open("config.json") as f:
        return int(json.load(f)["channels"]["transcript"])

def is_staff_or_owner(user):
    return any(role.id in [get_staff_role_id(), get_owner_role_id()] for role in user.roles)

def is_buyer(user):
    return any(role.id == get_buyer_role_id() for role in user.roles)