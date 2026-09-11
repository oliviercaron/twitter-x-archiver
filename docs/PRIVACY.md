# Privacy

X Archive is built around local storage. Posts, media, categories, notes, settings and exports are saved on your computer. Your personal archive is not uploaded to this GitHub repository or included in release packages.

To archive posts from your signed-in X account, the browser extension reads two X cookies: `auth_token` and `ct0`. It only requests these named cookies from `x.com` or `twitter.com`. They are sent to the companion service running on your own computer at `127.0.0.1`, where they are kept in the local archive folder so the service can make the requests you asked for.

These cookie values are not included in CSV exports, returned by the local API or intentionally written to logs. They are sensitive and should be treated like a password. You can remove the saved X connection from the app.

The local service contacts X and its media hosts to retrieve selected posts, replies, images and videos. X therefore receives normal network requests associated with your account and connection. The extension does not add analytics or send your archive to a separate storage service.

When reporting a problem, never post your cookies, `session.json`, browser profile, raw request headers or an archive containing private material. A screenshot with sample data and a description of the error is usually enough.

