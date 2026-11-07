/* Run manually as administrator in SSMS after creating a dedicated SQL login
   named qb_qc_reader (Security > Logins > New Login, SQL authentication).
   Use a unique password; do not grant server roles. Mixed-mode SQL authentication
   must be enabled if this server currently accepts Windows authentication only.
   Never use the pipeline login or your own Windows administrator identity. */
USE [QBAccounting];
GO
IF SUSER_ID(N'qb_qc_reader') IS NULL
    THROW 50000, 'Create the dedicated qb_qc_reader SQL login first.', 1;
IF USER_ID(N'qb_qc_reader') IS NULL
    CREATE USER [qb_qc_reader] FOR LOGIN [qb_qc_reader];
GO
GRANT CONNECT TO [qb_qc_reader];
DENY INSERT, UPDATE, DELETE, EXECUTE, ALTER, TAKE OWNERSHIP TO [qb_qc_reader];
GRANT SELECT ON OBJECT::qb.AccountBalances TO [qb_qc_reader];
GRANT SELECT ON OBJECT::qb.Companies TO [qb_qc_reader];
GRANT SELECT ON OBJECT::qb.ChartOfAccounts TO [qb_qc_reader];
GRANT SELECT ON OBJECT::qb.JournalEntries TO [qb_qc_reader];
GRANT SELECT ON OBJECT::qb.JournalEntryLines TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.vw_StatementStatus TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.AccountLink TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.vw_QBReconDetail TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.Statements TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.Findings TO [qb_qc_reader];
GRANT SELECT ON OBJECT::agent.Messages TO [qb_qc_reader];
GRANT SELECT ON OBJECT::fin.SplitMonths TO [qb_qc_reader];
GO
/* Read-only verification. All write checks below must be 0. */
EXECUTE AS USER = 'qb_qc_reader';
SELECT HAS_PERMS_BY_NAME('qb.JournalEntries','OBJECT','SELECT') AS CanRead,
       HAS_PERMS_BY_NAME('qb.JournalEntries','OBJECT','UPDATE') AS CanUpdate,
       HAS_PERMS_BY_NAME('fin.Findings','OBJECT','INSERT') AS CanInsert,
       HAS_PERMS_BY_NAME('agent.Messages','OBJECT','DELETE') AS CanDelete;
REVERT;
GO
