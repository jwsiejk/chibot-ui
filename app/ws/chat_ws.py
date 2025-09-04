from flask import Response
def register_ws_route(app):
    @app.route('/ws/v1/chat', methods=['GET'])
    def ws_upgrade_only():
        headers={'Connection':'Upgrade','Upgrade':'websocket'}
        return Response('WebSocket upgrade required', status=426, headers=headers, mimetype='text/plain')
