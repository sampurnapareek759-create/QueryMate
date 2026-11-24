-- Run once in MySQL as root. Creates a SELECT-only user for QueryMate (second safety layer).
-- Replace CHANGE_ME with your own password and put the same one in .env (DB_PASSWORD).
CREATE USER IF NOT EXISTS 'querymate_ro'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT ON chinook.*   TO 'querymate_ro'@'localhost';
GRANT SELECT ON sakila.*    TO 'querymate_ro'@'localhost';
GRANT SELECT ON world.*     TO 'querymate_ro'@'localhost';
GRANT SELECT ON employees.* TO 'querymate_ro'@'localhost';
FLUSH PRIVILEGES;
