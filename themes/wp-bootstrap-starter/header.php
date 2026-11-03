<?php
/**
 * Site header: <head>, navbar, opening layout wrappers.
 *
 * Extensibility hooks: 'wbs_before_header', 'wbs_after_header', 'wbs_before_main'.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;
?>
<!doctype html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="<?php bloginfo( 'charset' ); ?>">
<meta name="viewport" content="width=device-width, initial-scale=1">
<?php wp_head(); ?>
</head>

<body <?php body_class( 'd-flex flex-column min-vh-100' ); ?>>
<?php wp_body_open(); ?>

<?php do_action( 'wbs_before_header' ); ?>

<a class="skip-link visually-hidden-focusable" href="#main"><?php esc_html_e( 'Skip to content', WBS_TEXTDOMAIN ); ?></a>

<header class="site-header">
	<nav class="navbar navbar-expand-lg <?php echo esc_attr( get_theme_mod( 'wbs_navbar_scheme', 'navbar-dark bg-dark' ) ); ?>" aria-label="<?php esc_attr_e( 'Primary', WBS_TEXTDOMAIN ); ?>">
		<div class="<?php echo esc_attr( get_theme_mod( 'wbs_container', 'container' ) ); ?>">
			<?php if ( has_custom_logo() ) : ?>
				<?php the_custom_logo(); ?>
			<?php else : ?>
				<a class="navbar-brand" href="<?php echo esc_url( home_url( '/' ) ); ?>"><?php bloginfo( 'name' ); ?></a>
			<?php endif; ?>

			<button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#primaryNav" aria-controls="primaryNav" aria-expanded="false" aria-label="<?php esc_attr_e( 'Toggle navigation', WBS_TEXTDOMAIN ); ?>">
				<span class="navbar-toggler-icon"></span>
			</button>

			<div class="collapse navbar-collapse" id="primaryNav">
				<?php
				wp_nav_menu(
					array(
						'theme_location' => 'primary',
						'container'      => false,
						'menu_class'     => 'navbar-nav ms-auto mb-2 mb-lg-0',
						'fallback_cb'    => 'wbs_menu_fallback',
						'walker'         => new WBS_Bootstrap_Nav_Walker(),
						'depth'          => 2,
					)
				);
				?>
			</div>
		</div>
	</nav>
</header>

<?php do_action( 'wbs_after_header' ); ?>

<main id="main" class="site-main flex-grow-1 py-4">
	<?php do_action( 'wbs_before_main' ); ?>
	<div class="<?php echo esc_attr( get_theme_mod( 'wbs_container', 'container' ) ); ?>">
