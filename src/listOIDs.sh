#!/bin/bash

python3 -c "
import requests, urllib3
urllib3.disable_warnings()
r = requests.get(
    'https://{CUC}/vmrest/handlers/callhandlers',
    auth=('admin', 'password'),
    headers={'Accept': 'application/json'},
    verify=False
)
for h in r.json().get('Callhandler', []):
    print(h['ObjectId'], ' ', h['DisplayName'])
" | grep -i "hillcrest"