// Executed only inside the MongoDB container; credentials never enter argv/logs.
const fs = require('fs');
const config = JSON.parse(fs.readFileSync('/bootstrap/credentials.json', 'utf8'));
const connection = new Mongo('mongodb://127.0.0.1:27017');
const admin = connection.getDB('admin');
if (!admin.auth(config.admin_user, config.admin_password)) {
  throw new Error('MongoDB admin authentication failed');
}
const application = connection.getDB(config.database);
const existing = application.getUser(config.app_user);
if (!existing) {
  application.createUser({user: config.app_user, pwd: config.app_password,
    roles: [{role: 'readWrite', db: config.database}]});
  print('CREATED');
} else {
  // Refuse to silently rotate passwords or grant broader permissions on rerun.
  if (existing.roles.length !== 1 || existing.roles[0].role !== 'readWrite' ||
      existing.roles[0].db !== config.database) {
    throw new Error('Existing application user has unexpected roles');
  }
}
const appConnection = new Mongo('mongodb://127.0.0.1:27017');
const appDatabase = appConnection.getDB(config.database);
if (!appDatabase.auth(config.app_user, config.app_password)) {
  throw new Error('Application password does not match existing database; rotate explicitly');
}
// An authenticated read requires the intended database role; ping alone would not.
appDatabase.getCollection('trx_collection').findOne({});
print('VERIFIED');
