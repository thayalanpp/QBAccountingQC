/* Run manually in SSMS as administrator.
   FIRST create a new SQL-authentication login qb_qc_explorer with a unique password
   using SSMS Security > Logins. Assign no server roles. Do not reuse an admin login.
   This script creates a user only; it never modifies accounting data.
   Skip this if your existing qb_agent_sql identity already passes the reader check. */
USE [QBAccounting];
GO
IF SUSER_ID(N'qb_qc_explorer') IS NULL
    THROW 50000, 'Create qb_qc_explorer SQL login in SSMS first.', 1;
IF USER_ID(N'qb_qc_explorer') IS NULL
    CREATE USER [qb_qc_explorer] FOR LOGIN [qb_qc_explorer];
GO
GRANT CONNECT TO [qb_qc_explorer];
GRANT SELECT ON DATABASE::[QBAccounting] TO [qb_qc_explorer];
GRANT VIEW DEFINITION ON DATABASE::[QBAccounting] TO [qb_qc_explorer];
DENY INSERT,UPDATE,DELETE,EXECUTE,ALTER,TAKE OWNERSHIP TO [qb_qc_explorer];
GO
