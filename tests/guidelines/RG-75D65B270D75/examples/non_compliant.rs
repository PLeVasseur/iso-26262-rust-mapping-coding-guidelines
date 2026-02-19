fn main() {
    // Intentional compile failure for negative evidence.
    let must_be_u32: u32 = "invalid";
    let _ = must_be_u32;
}
