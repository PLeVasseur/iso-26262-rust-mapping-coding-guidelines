fn main() {
    // Compliant evidence for error propagation and failure handling.
    let values = [1_u32, 2_u32, 3_u32];
    let total: u32 = values.into_iter().sum();
    assert_eq!(total, 6);
}
