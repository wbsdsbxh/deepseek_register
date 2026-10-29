# deepseek_pow_solver.wasm

来源：https://github.com/AmanCode22/deepseek_pow_solver
版本：v1.0.0 (`deepseek_pow_solver.wasm` release 资产)

导出函数：
- `alloc(size: usize) -> *mut u8`
- `solve_pow(challenge_hex_ptr, challenge_hex_len, salt_ptr, salt_len, expire_at: u64, difficulty: u64) -> u64`

solve_pow 返回 nonce，满足 `sha3_256("{salt}_{expire_at}_{nonce}") == challenge`。
返回 0xFFFFFFFFFFFFFFFF 表示在 difficulty 次迭代内无解。
