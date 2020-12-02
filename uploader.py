from dropbox import Dropbox
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata, WriteMode
from garmin_uploader.workflow import Workflow
import redis
from flask import Flask, Response, request
from hashlib import sha256
import hmac
import threading
import json
import os, sys

DROPBOX_APP_SECRET = os.environ.get('DROPBOX_APP_SECRET')
DROPBOX_ACCESS_TOKEN = os.environ.get('DROPBOX_ACCESS_TOKEN')
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')

if not DROPBOX_APP_SECRET or not DROPBOX_ACCESS_TOKEN:
    print('DROPBOX_APP_SECRET or DROPBOX_ACCESS_TOKEN env vars missing')
    sys.exit()

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
app = Flask(__name__)
@app.route('/g', methods=['GET'])
def verify():
    '''Respond to the webhook verification (GET request) by echoing back the challenge parameter.'''
    resp = Response(request.args.get('challenge'))
    resp.headers['Content-Type'] = 'text/plain'
    resp.headers['X-Content-Type-Options'] = 'nosniff'

    return resp


@app.route('/g', methods=['POST'])
def webhook():
    '''Receive a list of changed user IDs from Dropbox and process each.'''

    # Make sure this is a valid request from Dropbox
    signature = request.headers.get('X-Dropbox-Signature')
    if not hmac.compare_digest(signature, hmac.new(bytes(DROPBOX_APP_SECRET.encode('utf-8')), request.data, sha256).hexdigest()):
        abort(403)

    for account in request.json['list_folder']['accounts']:
        # We need to respond quickly to the webhook request, so we do the
        # actual work in a separate thread. For more robustness, it's a
        # good idea to add the work to a reliable queue and process the queue
        # in a worker process.
        threading.Thread(target=process_user, args=(account,)).start()
    return ''


def process_user(account):
    '''Call /files/list_folder for the given user ID and process any changes.'''

    # OAuth token for the user
    token = redis_client.hget('tokens', account)

    # cursor for the user (None the first time)
    cursor = redis_client.hget('cursors', account)

    dbx = Dropbox(DROPBOX_ACCESS_TOKEN)
    has_more = True

    while has_more:
        if cursor is None:
            result = dbx.files_list_folder(path='/Aplicaciones/WahooFitness')
        else:
            result = dbx.files_list_folder_continue(cursor)

        for entry in result.entries:
            print('entry', entry)

            # solo archivos creados que terminan en .fit
            if (isinstance(entry, FileMetadata) and entry.path_lower.endswith('.fit')):
                print('upload fit', entry.name)
                md, res = dbx.files_download(entry.id)
                with open(entry.name, 'wb') as f:
                    data = f.write(res.content)
                upload_to_garmin([entry.name])

        # Update cursor
        cursor = result.cursor
        redis_client.hset('cursors', account, cursor)

        # Repeat only if there's more to do
        has_more = result.has_more

def upload_to_garmin(paths, username=None, password=None, activity_name=None, activity_type=None, verbose=2):
    workflow = Workflow(activity_name=activity_name, activity_type=activity_type, password=password, paths=paths, username=username, verbose=2)
    workflow.run()


if __name__ == "__main__":
        app.run(host='0.0.0.0')
