<?php
/**
 * Customizer: a small set of theme options proving the extension pattern.
 * Add your own settings/controls to wbs_customize_register().
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

add_action( 'customize_register', 'wbs_customize_register' );
/**
 * Register theme options.
 *
 * @param WP_Customize_Manager $wp_customize Customizer manager.
 */
function wbs_customize_register( $wp_customize ) {
	$wp_customize->add_section(
		'wbs_theme_options',
		array(
			'title'    => __( 'Theme Options', WBS_TEXTDOMAIN ),
			'priority' => 30,
		)
	);

	// Layout width.
	$wp_customize->add_setting(
		'wbs_container',
		array(
			'default'           => 'container',
			'sanitize_callback' => 'wbs_sanitize_container',
		)
	);
	$wp_customize->add_control(
		'wbs_container',
		array(
			'label'   => __( 'Layout width', WBS_TEXTDOMAIN ),
			'section' => 'wbs_theme_options',
			'type'    => 'select',
			'choices' => array(
				'container'       => __( 'Fixed width', WBS_TEXTDOMAIN ),
				'container-fluid' => __( 'Full width', WBS_TEXTDOMAIN ),
			),
		)
	);

	// Navbar color scheme.
	$wp_customize->add_setting(
		'wbs_navbar_scheme',
		array(
			'default'           => 'navbar-dark bg-dark',
			'sanitize_callback' => 'wbs_sanitize_navbar_scheme',
		)
	);
	$wp_customize->add_control(
		'wbs_navbar_scheme',
		array(
			'label'   => __( 'Navbar color scheme', WBS_TEXTDOMAIN ),
			'section' => 'wbs_theme_options',
			'type'    => 'select',
			'choices' => array(
				'navbar-dark bg-dark'    => __( 'Dark', WBS_TEXTDOMAIN ),
				'navbar-light bg-light'  => __( 'Light', WBS_TEXTDOMAIN ),
				'navbar-dark bg-primary' => __( 'Primary color', WBS_TEXTDOMAIN ),
			),
		)
	);

	// Footer credit line (empty = automatic default).
	$wp_customize->add_setting(
		'wbs_footer_text',
		array(
			'default'           => '',
			'sanitize_callback' => 'wp_kses_post',
		)
	);
	$wp_customize->add_control(
		'wbs_footer_text',
		array(
			'label'       => __( 'Footer text', WBS_TEXTDOMAIN ),
			'description' => __( 'Leave empty for the automatic copyright line. Basic HTML allowed.', WBS_TEXTDOMAIN ),
			'section'     => 'wbs_theme_options',
			'type'        => 'textarea',
		)
	);
}

/**
 * Sanitize the layout-width setting.
 *
 * @param string $value Raw value.
 * @return string
 */
function wbs_sanitize_container( $value ) {
	return in_array( $value, array( 'container', 'container-fluid' ), true ) ? $value : 'container';
}

/**
 * Sanitize the navbar scheme setting.
 *
 * @param string $value Raw value.
 * @return string
 */
function wbs_sanitize_navbar_scheme( $value ) {
	$allowed = array( 'navbar-dark bg-dark', 'navbar-light bg-light', 'navbar-dark bg-primary' );
	return in_array( $value, $allowed, true ) ? $value : 'navbar-dark bg-dark';
}
