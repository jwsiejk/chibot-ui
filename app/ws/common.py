import json
def json_dumps(obj): 
    return json.dumps(obj, separators=(",",":"))
